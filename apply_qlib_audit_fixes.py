#!/usr/bin/env python3
"""Guarded, idempotence-detecting patch for AT2018cow/qlib main @ f0f7709.

Run from the root of a checkout: python apply_qlib_audit_fixes.py --check
                                    python apply_qlib_audit_fixes.py --apply
Does not call GitHub, start jobs or modify main remotely.
"""
from __future__ import annotations

import argparse
import ast
from pathlib import Path
import shutil
import tempfile


def replace_once(text: str, old: str, new: str, name: str) -> str:
    count = text.count(old)
    if name == "daily rank-only warning" and count == 2:
        return text.replace(old, new, 1)
    if count != 1:
        raise RuntimeError(f"Patch {name}: expected ONE exact source anchor, got {count}; no files changed")
    return text.replace(old, new, 1)


def edit_function(source: str, name: str, changes) -> str:
    start_token = f"def {name}("
    start = source.find(start_token)
    if start < 0 or source.find(start_token, start + 1) >= 0:
        raise RuntimeError(f"Missing/ambiguous function {name}")
    tree = ast.parse(source)
    nodes = [n for n in tree.body if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef)) and n.name == name]
    if len(nodes) != 1:
        raise RuntimeError(f"AST cannot identify unique top-level {name}")
    node = nodes[0]
    lines = source.splitlines(keepends=True)
    start_idx = node.lineno - 1
    end_idx = node.end_lineno
    chunk = "".join(lines[start_idx:end_idx])
    modified = changes(chunk)
    if modified == chunk:
        raise RuntimeError(f"Patch for {name} produced no changes")
    lines[start_idx:end_idx] = [modified]
    return "".join(lines)


def patch(source: str, health: str) -> tuple[str, str]:
    if "from qlib_audit_fixes import" in source:
        raise RuntimeError("This checkout appears already patched; not applying twice")

    def patch_config(s):
        s = replace_once(
            s, '    from ruamel.yaml import YAML\n',
            '    if sum(bool(x) for x in (label20, label40, label60)) > 1:\n'
            '        raise ValueError("Choose exactly one prediction horizon")\n'
            '    if (long_train or verify) and not recent:\n'
            '        raise ValueError("long_train and verify require recent=True")\n'
            '    from ruamel.yaml import YAML\n', 'invalid option combinations')
        s = replace_once(
            s, '    # 7) 基本面因子模式：Alpha158 + $roe 等 6 字段\n',
            '    # Explicit market selection must win over --enhanced and update both benchmarks.\n'
            '    if market is not None:\n'
            '        _indices = {"csi300": "SH000300", "csi500": "SH000905", "csi1000": "SH000852"}\n'
            '        if market not in _indices:\n'
            '            raise ValueError(f"Unsupported market: {market}")\n'
            '        cfg["task"]["dataset"]["kwargs"]["handler"]["kwargs"]["instruments"] = market\n'
            '        cfg["market"] = market\n'
            '        cfg["benchmark"] = _indices[market]\n'
            '        cfg["port_analysis_config"]["backtest"]["benchmark"] = _indices[market]\n'
            '    # 7) 基本面因子模式：Alpha158 + $roe 等 6 字段\n',
            'enhanced market precedence')
        s = replace_once(
            s, '"kwargs": {"signal": "<PRED>", "topk": 50, "n_drop": 2, "rebalance_days": 20},',
            '"kwargs": {"signal": "<PRED>", "topk": topk if topk is not None else 50, '
            '"n_drop": nd if nd is not None else 2, "rebalance_days": 20},',
            'rolling strategy parameters')
        addition = (
            "    # A sample's Ref(...,-h) label must mature before the NEXT stage starts.\n"
            "    # This also purges the validation tail used by LightGBM early stopping.\n"
            "    if recent:\n"
            "        _cal = read_trading_calendar(provider_dir or DATA_DIR)\n"
            "        purge_cfg_splits(cfg, _cal)\n"
            "    if smoke and enhanced:\n"
            "        _model_kw = cfg['task']['model']['kwargs']\n"
            "        if 'early_stop' in _model_kw:\n"
            "            _model_kw['early_stop'] = 2\n"
            "    return cfg\n"
        )
        return replace_once(s, '    return cfg\n', addition, "config tail")

    source = replace_once(
        source, "from pathlib import Path\n",
        "from pathlib import Path\nfrom qlib_audit_fixes import read_trading_calendar, purge_cfg_splits\n",
        "import split guards",
    )
    source = edit_function(source, "_load_and_patch_cfg", patch_config)

    def patch_worker(s):
        anchor = '    seg["test"] = [te_s, te_e]\n'
        injected = (anchor +
                    "    # Validation targets near the boundary cannot use test-period closes.\n"
                    "    purge_cfg_splits(cfg, read_trading_calendar(DATA_DIR), horizon=20)\n")
        return replace_once(s, anchor, injected, "batch C split purge")
    source = edit_function(source, "batch_c_window", patch_worker)

    def patch_p2(s):
        s = replace_once(
            s, '    for w_idx, (tr_s, tr_e, va_s, va_e, te_s, te_e) in enumerate(ROLLING_WINDOWS, 1):\n',
            '    _latest = _latest_trading_day()\n'
            '    _windows = _gen_5y_windows()[-7:]\n'
            '    for w_idx, (tr_s, tr_e, va_s, va_e, te_s, te_e) in enumerate(_windows, 1):\n'
            '        if te_s > _latest:\n'
            '            continue\n'
            '        te_e = min(te_e, _latest)\n', "P2 undefined windows and end clamp")
        anchor = '        seg["test"] = [te_s, te_e]\n'
        s = replace_once(s, anchor, anchor +
            "        purge_cfg_splits(cfg, read_trading_calendar(DATA_DIR), horizon=20)\n", "P2 split purge")
        s = replace_once(s, '    full = pd.concat(report_parts).sort_index()\n',
                         '    if not report_parts:\n'
                         '        raise RuntimeError("P2: no valid test windows")\n'
                         '    full = pd.concat(report_parts).sort_index()\n', 'P2 missing reports')
        return s
    source = edit_function(source, "p2_rolling", patch_p2)

    def patch_tune(s):
        s = replace_once(s, '    pred = model.predict(dataset)\n',
                         '    pred = model.predict(dataset, segment="valid")\n', "tuning predictions must use valid")
        s = replace_once(s, 'dataset.prepare("test", col_set=["feature", "label"], data_key=DataHandlerLP.DK_L)',
                         'dataset.prepare("valid", col_set=["feature", "label"], data_key=DataHandlerLP.DK_L)',
                         "tuning labels must use valid")
        return s
    source = edit_function(source, "tune_one", patch_tune)

    def patch_prepare(s):
        return replace_once(s, '    print(r.stderr[-2000:])\n    vol.commit()\n',
                            '    print(r.stderr[-2000:])\n'
                            '    if r.returncode != 0:\n'
                            '        raise RuntimeError(f"Data health subprocess failed: exit={r.returncode}")\n'
                            '    vol.commit()\n', "subprocess exit code")
    source = edit_function(source, "prepare_data", patch_prepare)

    def patch_debug(s):
        s = replace_once(s, '    import qlib\n    from qlib.data import D\n',
                         '    import pandas as pd\n    import qlib\n    from qlib.data import D\n', 'debug pandas import')
        s = replace_once(s, '    import pandas as pd\n\n    df = D.features(', '\n    df = D.features(', 'debug late import')
        return s
    source = edit_function(source, "debug_data", patch_debug)

    def patch_vcheck_main(s):
        return replace_once(s, "res_0916['matrix_0916'][k]['excess_with_cost_annual']",
                            "res_0916['matrix_0916'][f'{k}_ndrop3']['excess_with_cost_annual']",
                            "vcheck matrix name")
    source = edit_function(source, "main", patch_vcheck_main)

    def patch_daily(s):
        s = replace_once(s, '    top = day.sort_values(ascending=False).head(topk)\n',
                         '    # Ranking only: n_drop requires current holdings and an execution-day order planner.\n'
                         '    print("[daily] RANKING ONLY: not executable orders; nd does not apply to ranking CSV")\n'
                         '    top = day.sort_values(ascending=False).head(topk)\n', "daily rank-only warning")
        return replace_once(
            s, '            "csv_content": csv_content, "data_calendar_end": cal_lines[-1]}\n',
            '            "csv_content": csv_content, "data_calendar_end": cal_lines[-1],\n'
            '            "ranking_only": True, "rebalance_applied": False}\n', "daily result metadata")
    source = edit_function(source, "daily_standalone", patch_daily)

    def patch_cron(s):
        s = replace_once(s,
            '    if res["date"] != res["data_calendar_end"]:\n'
            '        print("[cron] 注：信号日期早于数据日历末日（节假日/数据延迟），照常入库留痕")\n',
            '    if res["date"] != res["data_calendar_end"]:\n'
            '        raise RuntimeError("Signal date does not match last data calendar date")\n'
            '    from datetime import datetime\n'
            '    from zoneinfo import ZoneInfo\n'
            '    today = datetime.now(ZoneInfo("Asia/Shanghai")).date().isoformat()\n'
            '    if res["date"] != today:\n'
            '        raise RuntimeError(f"Today={today} but latest dataset={res[\'date\']}; "\n'
            '                           "possible exchange holiday or stale release; do not publish old ranking")\n',
            'cron stale signals')
        s = replace_once(s,
            '    sha = exist.json().get("sha") if exist.status_code == 200 else None\n',
            '    if exist.status_code == 200:\n'
            '        raise RuntimeError("Same-date signal changed: refusing to rewrite immutable paper-trading record")\n'
            '    if exist.status_code != 404:\n'
            '        raise RuntimeError(f"Cannot check existing signal: HTTP {exist.status_code}")\n'
            '    sha = None\n', 'cron immutable record')
        s = replace_once(s,
            '        print(f"[cron] ❌ GitHub 推送失败: {r.status_code} {r.text[:200]}")\n',
            '        raise RuntimeError(f"GitHub push failed: HTTP {r.status_code}: {r.text[:200]}")\n',
            'cron failure propagation')
        return s
    source = edit_function(source, "daily_cron", patch_cron)

    def patch_p1(s):
        s = replace_once(s,
            '                                                        names=["datetime", "instrument"])).dropna()\n',
            '                                                        names=["datetime", "instrument"])).droplevel("datetime").dropna()\n',
            'weighted return index')
        s = replace_once(s, '            gross = float((w * f).sum())\n',
                         '            w = w / w.sum()  # Re-normalize after excluding missing future prices\n'
                         '            gross = float((w * f).sum())\n', 'weighted normalization')
        left = '            turnover = float((w.reindex(prev_w.index).fillna(0) - prev_w.reindex(w.index).fillna(0)).abs().sum()) \\\n                if len(prev_w) else 1.0\n'
        right = ('            universe = w.index.union(prev_w.index)\n'
                 '            turnover = float((w.reindex(universe, fill_value=0) -\n'
                 '                              prev_w.reindex(universe, fill_value=0)).abs().sum())\n')
        s = replace_once(s, left, right, 'weighted turnover union')
        return replace_once(s, '"ann_excess_sim": round(ann, 4)',
                            '"ann_portfolio_sim": round(ann, 4)', "weighted gross vs excess")
    source = edit_function(source, "p1_diagnostics", patch_p1)

    health = replace_once(health,
        '            check_large_step_changes_result is not None\n            or check_large_step_changes_result is not None\n',
        '            check_missing_data_result is not None\n            or check_large_step_changes_result is not None\n',
        "data health missing-data gate")
    ast.parse(source)
    ast.parse(health)
    return source, health


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--check", action="store_true", help="validate all anchors and syntax; write nothing")
    p.add_argument("--apply", action="store_true", help="create .audit-prepatch.bak files and apply")
    args = p.parse_args()
    if args.check == args.apply:
        p.error("Choose exactly one of --check or --apply")
    root = Path.cwd()
    target = root / "modal_qlib_cn_a10g.py"
    health_path = root / "scripts" / "check_data_health.py"
    helper = root / "qlib_audit_fixes.py"
    package_helper = Path(__file__).with_name("qlib_audit_fixes.py")
    if not target.exists() or not health_path.exists() or not package_helper.exists():
        raise SystemExit("Run in repository root, with both source files and patch package available")
    old_main, old_health = target.read_text(), health_path.read_text()
    new_main, new_health = patch(old_main, old_health)
    compile(new_main, str(target), "exec")
    compile(new_health, str(health_path), "exec")
    print("PASS: all strict anchors matched; both patched files compile")
    if args.check:
        return
    if helper.exists() and helper.read_bytes() != package_helper.read_bytes():
        raise SystemExit("Conflicting qlib_audit_fixes.py exists; will not overwrite")
    for filepath in (target, health_path):
        backup = filepath.with_name(filepath.name + ".audit-prepatch.bak")
        if backup.exists():
            raise SystemExit(f"Backup already exists: {backup}; refusing to overwrite")
    for filepath in (target, health_path):
        shutil.copy2(filepath, filepath.with_name(filepath.name + ".audit-prepatch.bak"))
    for filepath, text in ((target, new_main), (health_path, new_health)):
        with tempfile.NamedTemporaryFile("w", dir=filepath.parent, delete=False) as out:
            out.write(text)
            tmp = Path(out.name)
        tmp.replace(filepath)
    if not helper.exists():
        shutil.copy2(package_helper, helper)
    print("Applied. main branch untouched remotely; original files saved as .audit-prepatch.bak")


if __name__ == "__main__":
    main()
