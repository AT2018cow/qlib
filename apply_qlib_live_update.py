#!/usr/bin/env python3
"""Strictly anchored, one-time source upgrade for fix/qlib-audit-20260919.

--check validates anchors + compiles an in-memory modified program; --apply
writes it with a backup. Never touches git/main by itself.
"""
from __future__ import annotations

import argparse
import ast
from pathlib import Path


def replace_once(text: str, old: str, new: str, name: str) -> str:
    n = text.count(old)
    if n != 1:
        raise RuntimeError(f'{name}: expected exactly one anchor, found {n}')
    return text.replace(old, new, 1)


def edit_function(text: str, name: str, callback) -> str:
    tree = ast.parse(text)
    nodes = [node for node in tree.body if isinstance(node, ast.FunctionDef) and node.name == name]
    if len(nodes) != 1:
        raise RuntimeError(f'Cannot identify {name}')
    lines = text.splitlines(keepends=True)
    node = nodes[0]
    old = ''.join(lines[node.lineno-1:node.end_lineno])
    new = callback(old)
    if new == old:
        raise RuntimeError(f'No edits for {name}')
    lines[node.lineno-1:node.end_lineno] = [new]
    return ''.join(lines)


def patch(source: str) -> str:
    source = replace_once(source,
        'from qlib_audit_fixes import read_trading_calendar, purge_cfg_splits\n',
        'from qlib_audit_fixes import read_trading_calendar, purge_cfg_splits\n'
        'from qlib_live_retrain import (configure_asof, should_retrain, cache_signature,\n'
        '                               RETRAIN_EVERY_SESSIONS)\n', 'imports')
    # A persistent volume is necessary: the previous standalone daily container has
    # no durable model state, so checking a date without a persisted model is unsafe.
    source = replace_once(source,
        '@app.function(\n    cpu=CPU_COUNT,\n    memory=32768,\n    timeout=4 * 3600,\n)\ndef daily_standalone(',
        '@app.function(\n    volumes={str(VOL_ROOT): vol},\n    cpu=CPU_COUNT,\n    memory=32768,\n    timeout=4 * 3600,\n)\ndef daily_standalone(', 'persistent daily model volume')

    def daily(s):
        s = replace_once(s,
            '    import shutil\n    import tarfile\n\n    import numpy as np\n',
            '    import shutil\n    import tarfile\n    import hashlib\n    import json\n    import os\n    import pickle\n\n    import numpy as np\n', 'daily imports')
        old = '''    cfg = _load_and_patch_cfg(MODEL_CFG["lgb158"], smoke=False, recent=True, long_train=True, label20=True,
                              topk=topk, nd=nd, market=market, provider_dir=str(data_dir))
    model_obj = init_instance_by_config(cfg["task"]["model"], accept_types=Model)
    dataset = init_instance_by_config(cfg["task"]["dataset"], accept_types=Dataset)
    model_obj.fit(dataset)
    pred = model_obj.predict(dataset)
    predict_date = pred.index.get_level_values(0).max()
'''
        new = '''    cfg = _load_and_patch_cfg(MODEL_CFG["lgb158"], smoke=False, recent=True, long_train=True, label20=True,
                              topk=topk, nd=nd, market=market, provider_dir=str(data_dir))
    calendar = read_trading_calendar(data_dir)
    asof = calendar[-1]
    # Today is a feature/prediction date, NEVER a training/validation label date.
    live_split = configure_asof(cfg, calendar, asof, horizon=20)
    signature = cache_signature(cfg, horizon=20)
    cache_dir = VOL_ROOT / "live_models"
    cache_dir.mkdir(parents=True, exist_ok=True)
    model_file = cache_dir / f"{signature}.pkl"
    meta_file = cache_dir / f"{signature}.json"
    if model_file.exists() != meta_file.exists():
        raise RuntimeError("Partial model cache; refusing to load an unverified model")
    saved = json.loads(meta_file.read_text()) if meta_file.exists() else None
    if saved is not None and saved.get("signature") != signature:
        raise RuntimeError("Model cache signature mismatch")
    train_now = should_retrain(calendar, asof, saved["fit_asof"] if saved else None,
                               interval=RETRAIN_EVERY_SESSIONS)
    if train_now:
        model_obj = init_instance_by_config(cfg["task"]["model"], accept_types=Model)
        dataset = init_instance_by_config(cfg["task"]["dataset"], accept_types=Dataset)
        model_obj.fit(dataset)
        snapshot = {
            "signature": signature, "fit_asof": asof, "horizon": 20,
            "train": list(cfg["task"]["dataset"]["kwargs"]["segments"]["train"]),
            "valid": list(cfg["task"]["dataset"]["kwargs"]["segments"]["valid"]),
            "fit_start": cfg["task"]["dataset"]["kwargs"]["handler"]["kwargs"]["fit_start_time"],
            "fit_end": cfg["task"]["dataset"]["kwargs"]["handler"]["kwargs"]["fit_end_time"],
        }
        tmp_model = cache_dir / f".{signature}.{os.getpid()}.tmp"
        tmp_meta = cache_dir / f".{signature}.{os.getpid()}.json.tmp"
        try:
            with tmp_model.open("wb") as f:
                pickle.dump(model_obj, f, protocol=pickle.HIGHEST_PROTOCOL)
            snapshot["model_sha256"] = hashlib.sha256(tmp_model.read_bytes()).hexdigest()
            tmp_meta.write_text(json.dumps(snapshot, indent=2))
            os.replace(tmp_model, model_file)
            os.replace(tmp_meta, meta_file)
            vol.commit()
        finally:
            tmp_model.unlink(missing_ok=True)
            tmp_meta.unlink(missing_ok=True)
        saved = snapshot
        print(f"[daily] 模型已重训，训练结束={saved['train'][-1]} 验证结束={saved['valid'][-1]}")
    else:
        # Re-create the handler on fresh features but fit its processors ONLY on
        # the original model's training window. Otherwise daily refitting of
        # feature normalization changes the cached model's input distribution.
        if (saved.get("horizon") != 20 or not saved.get("model_sha256") or
                saved.get("fit_end") != saved.get("train", [None, None])[-1]):
            raise RuntimeError("Invalid cached model split metadata")
        if hashlib.sha256(model_file.read_bytes()).hexdigest() != saved["model_sha256"]:
            raise RuntimeError("Corrupt cached model; refusing unsafe inference")
        opts = cfg["task"]["dataset"]["kwargs"]
        opts["segments"]["train"] = saved["train"]
        opts["segments"]["valid"] = saved["valid"]
        opts["handler"]["kwargs"]["fit_start_time"] = saved["fit_start"]
        opts["handler"]["kwargs"]["fit_end_time"] = saved["fit_end"]
        # Today's only test row has no matured label and is never used in fit.
        opts["segments"]["test"] = [asof, asof]
        opts["handler"]["kwargs"]["end_time"] = asof
        dataset = init_instance_by_config(cfg["task"]["dataset"], accept_types=Dataset)
        with model_file.open("rb") as f:
            model_obj = pickle.load(f)
        print(f"[daily] 复用模型，训练日期={saved['fit_asof']}，距今未满 {RETRAIN_EVERY_SESSIONS} 交易日")
    pred = model_obj.predict(dataset, segment="test")
    if pred.empty:
        raise RuntimeError(f"No inference predictions for {asof}")
    predict_date = pred.index.get_level_values(0).max()
    if str(predict_date)[:10] != asof:
        raise RuntimeError(f"Inference date {predict_date} != latest bar {asof}")
'''
        s = replace_once(s, old, new, 'monthly retraining and cached inference')
        s = replace_once(s,
            '            "ranking_only": True, "rebalance_applied": False}',
            '            "ranking_only": True, "rebalance_applied": False,\n'
            '            "model_fit_asof": saved["fit_asof"],\n'
            '            "train_end": saved["train"][-1],\n'
            '            "valid_end": saved["valid"][-1],\n'
            '            "retrained_today": train_now}', 'daily audit metadata')
        return s
    source = edit_function(source, 'daily_standalone', daily)

    def tune(s):
        s = replace_once(s,
            '    _ensure_data()\n    label20 = horizon == 20\n    label60 = horizon == 60\n    cfg = _load_and_patch_cfg(\n        MODEL_CFG["lgb360"], smoke=False, recent=True, long_train=True, label20=label20, label60=label60\n    )\n',
            '    if horizon != 20:\n'
            '        raise ValueError("Production hyperparameter tuning is restricted to the 20-day Alpha158 target")\n'
            '    _ensure_data()\n'
            '    cfg = _load_and_patch_cfg(\n'
            '        MODEL_CFG["lgb158"], smoke=False, recent=True, long_train=True,\n'
            '        label20=True, market="csi1000", topk=20, nd=2\n'
            '    )\n', 'align tuning with production model')
        s = replace_once(s, '    pred = model.predict(dataset, segment="valid")\n',
            '    pred = model.predict(dataset, segment="valid")\n'
            '    if pred.empty:\n'
            '        raise RuntimeError("Empty validation predictions in hyperparameter search")\n', 'empty validation guard')
        s = replace_once(s,
            '    rank_ic = float(ic.dropna().mean())  # float 化，避免 Series 格式化报错\n'
            '    print(f"[tune] params={params} rank_ic={rank_ic:.4f}")\n'
            '    return {"rank_ic": rank_ic, "params": params}\n',
            '    rank_ic = float(ic.dropna().mean())\n'
            '    # Portfolio objective must match the actual daily strategy, not merely IC.\n'
            '    # Backtest ONLY on validation dates; test remains untouched for evaluation.\n'
            '    from qlib.backtest import backtest as normal_backtest\n'
            '    strategy = {"class": "TopkDropoutStrategy", "module_path": "qlib.contrib.strategy",\n'
            '                "kwargs": {"signal": pred, "topk": 20, "n_drop": 2}}\n'
            '    executor = {"class": "SimulatorExecutor", "module_path": "qlib.backtest.executor",\n'
            '                "kwargs": {"time_per_step": "day", "generate_portfolio_metrics": True}}\n'
            '    valid_start, valid_end = cfg["task"]["dataset"]["kwargs"]["segments"]["valid"]\n'
            '    pm, _ = normal_backtest(\n'
            '        strategy=strategy, executor=executor, start_time=valid_start, end_time=valid_end,\n'
            '        account=100000000, benchmark="SH000852",\n'
            '        exchange_kwargs={"limit_threshold": 0.095, "deal_price": "close",\n'
            '                         "open_cost": 0.0005, "close_cost": 0.0015, "min_cost": 5})\n'
            '    report = pm["1day"][0]\n'
            '    if report.empty:\n'
            '        raise RuntimeError("Empty validation portfolio report")\n'
            '    excess = report["return"] - report["bench"] - report["cost"]\n'
            '    if not bool(np.isfinite(excess.to_numpy()).all()):\n'
            '        raise RuntimeError("Non-finite validation net excess")\n'
            '    net_ann = float(excess.mean() * 238)\n'
            '    print(f"[tune] params={params} validation_net_annual={net_ann:.4f} rank_ic={rank_ic:.4f}")\n'
            '    return {"rank_ic": rank_ic, "excess_with_cost_annual": net_ann, "params": params}\n',
            'validation net portfolio objective')
        return s
    source = edit_function(source, 'tune_one', tune)

    def tune_driver(s):
        s = replace_once(s,
            '    params_list = [_sample_params(rng) for _ in range(n_trials)]\n',
            '    if n_trials < 1:\n'
            '        raise ValueError("n_trials must be positive")\n'
            '    params_list = [{}] + [_sample_params(rng) for _ in range(n_trials - 1)]\n',
            'include actual YAML baseline')
        s = replace_once(s,
            '    results.sort(key=lambda r: r["rank_ic"], reverse=True)\n',
            '    results.sort(key=lambda r: r["excess_with_cost_annual"], reverse=True)\n',
            'select on portfolio validation net return')
        s = replace_once(s,
            '        _json.dump({"rank_ic": results[0]["rank_ic"], "params": results[0]["params"], "horizon": horizon}, f, indent=2)\n',
            '        _json.dump({"rank_ic": results[0]["rank_ic"],\n'
            '                    "excess_with_cost_annual": results[0]["excess_with_cost_annual"],\n'
            '                    "params": results[0]["params"], "horizon": horizon,\n'
            '                    "model": "lgb158", "market": "csi1000", "topk": 20, "n_drop": 2,\n'
            '                    "warning": "validation-selected candidate; independent OOS required before adoption"}, f, indent=2)\n',
            'record full tuning objective')
        s = replace_once(s,
            '        print(f"[tune] {i}. rank_ic={r[\'rank_ic\']:.4f} {r[\'params\']}")\n',
            '        print(f"[tune] {i}. net_annual={r[\'excess_with_cost_annual\']:.4f} "\n'
            '              f"rank_ic={r[\'rank_ic\']:.4f} {r[\'params\']}")\n',
            'driver output objective')
        return s
    source = edit_function(source, 'tune_driver', tune_driver)

    def main(s):
        s = replace_once(s,
            '        params_list = [_sample_params(rng) for _ in range(tune)]\n',
            '        params_list = [{}] + [_sample_params(rng) for _ in range(tune - 1)]\n',
            'main include actual baseline')
        s = replace_once(s,
            '        results.sort(key=lambda r: r["rank_ic"], reverse=True)\n',
            '        results.sort(key=lambda r: r["excess_with_cost_annual"], reverse=True)\n',
            'main sort on net portfolio')
        s = replace_once(s,
            '            print(f"[tune] {i}. rank_ic={r[\'rank_ic\']:.4f} {r[\'params\']}")\n',
            '            print(f"[tune] {i}. net_annual={r[\'excess_with_cost_annual\']:.4f} "\n'
            '                  f"rank_ic={r[\'rank_ic\']:.4f} {r[\'params\']}")\n',
            'main tune prints')
        s = replace_once(s,
            '        print(f"[tune] 最优参数（保存到 Volume 请用 tune_driver）: rank_ic={results[0][\'rank_ic\']:.4f} params={results[0][\'params\']}")\n',
            '        print(f"[tune] 验证期候选（需独立样本外复验）: "\n'
            '              f"net_annual={results[0][\'excess_with_cost_annual\']:.4f} "\n'
            '              f"rank_ic={results[0][\'rank_ic\']:.4f} params={results[0][\'params\']}")\n',
            'main candidate safety wording')
        return s
    source = edit_function(source, 'main', main)
    compile(source, 'modal_qlib_cn_a10g.py', 'exec')
    return source


def main():
    parser = argparse.ArgumentParser()
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument('--check', action='store_true')
    mode.add_argument('--apply', action='store_true')
    opts = parser.parse_args()
    path = Path('modal_qlib_cn_a10g.py')
    if not path.exists():
        parser.error('Run from qlib repository root')
    old = path.read_text()
    new = patch(old)
    print('PASS: strict source anchors matched and transformed Python compiles')
    if opts.apply:
        backup = path.with_suffix('.py.live-prepatch.bak')
        if backup.exists():
            raise RuntimeError(f'Backup already exists: {backup}')
        backup.write_text(old)
        path.write_text(new)
        print('Applied source patch; original backed up locally (do not commit backup)')


if __name__ == '__main__':
    main()
