"""Cost-aware extension driver for deterministic retraining-phase audits.

This module deliberately leaves freq_experiment.py unchanged because that file's
SHA256 is part of the reproducibility manifest. It reuses already-validated
non-zero phase results when their experiment context exactly matches the passing
gate, runs only missing phases through the canonical driver, then merges and
enriches the final audit artifact.

Standalone: defines its own image/app to avoid cross-file import issues
on Modal containers (freq_experiment.py excludes itself from add_local_dir).
"""

from pathlib import Path

import modal

APP_NAME = "phase-audit-extend"
VOL_NAME = "qlib-cn-data"
VOL_ROOT = Path("/vol")
DATA_DIR = VOL_ROOT / "cn"

vol = modal.Volume.from_name(VOL_NAME, create_if_missing=True)

image = (
    modal.Image.debian_slim(python_version="3.11")
    .apt_install("build-essential")
    .env({"MLFLOW_ALLOW_FILE_STORE": "true"})
    .pip_install(
        "numpy==1.26.4", "cython", "pandas==2.2.3", "pyyaml", "ruamel.yaml", "fire", "lightgbm", "mlflow",
        "dill", "filelock", "tqdm", "loguru", "joblib", "pyarrow", "pydantic-settings", "redis",
        "python-redis-lock", "pymongo", "gym", "cvxpy", "matplotlib", "jupyter", "nbconvert",
        "setuptools-scm", "akshare",
    )
    .pip_install("torch")
    .add_local_dir(
        ".",
        remote_path="/root/qlib",
        copy=True,
        ignore=lambda path: (
            str(path).endswith((".cpp", ".so"))
            or any(part in str(path) for part in (".venv", "mlruns", "__pycache__"))
            or str(path).startswith(".git/")
            or "/.git/" in str(path)
        ),
    )
    .run_commands(
        "cd /root/qlib && pip install . --no-build-isolation --no-deps",
        # Build Cy extensions in-place so freq_experiment.py's import of qlib.data works
        "cd /root/qlib && python -c \"from setuptools import setup, Extension; from Cython.Build import cythonize; import numpy; extensions = [Extension('qlib.data._libs.rolling', ['qlib/data/_libs/rolling.pyx'], language='c++', include_dirs=[numpy.get_include()]), Extension('qlib.data._libs.expanding', ['qlib/data/_libs/expanding.pyx'], language='c++', include_dirs=[numpy.get_include()])]; setup(ext_modules=cythonize(extensions, language_level='3'), script_args=['build_ext', '--inplace'])\"",
        "cp /root/qlib/freq_experiment.py /root/qlib/qlib_audit_fixes.py /root/qlib/qlib_live_retrain.py /root/qlib/board_rules.py /root/qlib/board_execution.py /root/",
    )
)

app = modal.App(APP_NAME, image=image)

# Import from freq_experiment at runtime (inside functions, after image is built)
# so that the module is available from /root/ on the container.


def _load_json(path):
    import json
    if not Path(path).is_file():
        return None
    return json.loads(Path(path).read_text())


def _same_phase_context(payload, baseline, *, freq, eval_from, market, topk, nd):
    if not payload:
        return False
    gate = payload.get("reproducibility_gate") or {}
    return (
        payload.get("protocol") == "retrain_phase_sensitivity_v2_repro"
        and payload.get("market") == market
        and int(payload.get("freq", -1)) == int(freq)
        and int(payload.get("topk", -1)) == int(topk)
        and int(payload.get("n_drop", -1)) == int(nd)
        and payload.get("eval_from") == eval_from
        and payload.get("reproducibility") == baseline.get("reproducibility")
        and gate.get("passed") is True
        and gate == (baseline.get("reproducibility_gate") or {})
    )


def _is_reusable_phase(result):
    if not result or result.get("source") != "deterministic_refit":
        return False
    return (
        int(result.get("n_retrains", 0)) > 0
        and bool(result.get("signal_sha256"))
        and bool((result.get("report_artifact") or {}).get("content_sha256"))
        and bool(result.get("performance"))
        and result.get("account_return_max_error") == 0
    )


def _enrich_phase_result(result):
    import pandas as pd
    from freq_experiment import _frame_sha256

    enriched = dict(result)
    perf = enriched.get("performance") or {}
    for key in ("benchmark_max_drawdown", "annual_volatility"):
        if key in perf:
            enriched[key] = perf[key]
    artifact = enriched.get("report_artifact") or {}
    path = Path(artifact.get("path", ""))
    if not path.is_file():
        raise RuntimeError(f"phase report artifact missing: {path}")
    report = pd.read_parquet(path)
    expected_content_hash = artifact.get("content_sha256")
    if not expected_content_hash or _frame_sha256(report) != expected_content_hash:
        raise RuntimeError(f"phase report content hash mismatch: {path}")
    return enriched


def _phase_metric_summary(results, field):
    import numpy as np

    values = [r.get(field) for r in results.values() if isinstance(r, dict) and r.get(field) is not None]
    if not values:
        return {}
    return {
        "n": len(values),
        "min": round(min(values), 6),
        "q25": round(float(np.percentile(values, 25)), 6),
        "median": round(float(np.median(values)), 6),
        "q75": round(float(np.percentile(values, 75)), 6),
        "max": round(max(values), 6),
    }


@app.function(volumes={str(VOL_ROOT): vol}, timeout=4 * 3600)
def run_canonical_phase_driver(freq: int, eval_from: str, market: str,
                               topk: int, nd: int, phases: str):
    """Run the canonical retrain-phase driver logic locally in this container.

    freq_experiment.py is available at /root/ so its module and helpers can be
    imported. The raw function is accessed via get_raw_f() which returns the
    undecorated Python callable without needing the other app to be running.
    """
    import sys as _sys

    _sys.path.insert(0, "/root")
    _sys.path.insert(0, "/root/qlib")
    import freq_experiment as _fe

    # Run the canonical driver as a subprocess to avoid cross-app Modal hydration issues.
    # freq_experiment.py is at /root/ and its module-level code works in this container.
    import subprocess as _sp
    import json as _json_mod
    _script = (
        "import sys, json\n"
        "sys.path.insert(0, '/root')\n"
        "sys.path.insert(0, '/root/qlib')\n"
        "from freq_experiment import retrain_phase_sensitivity_driver\n"
        "# Access raw function directly (avoids hydration check that\n"
        "# requires the defining app to be running)\n"
        "_mf = retrain_phase_sensitivity_driver\n"
        "raw_f = None\n"
        "for attr in ('_user_function', '__wrapped__', '_fn', '_f'):\n"
        "    v = getattr(_mf, attr, None)\n"
        "    if v is not None and callable(v):\n"
        "        raw_f = v\n"
        "        break\n"
        "if raw_f is None:\n"
        "    raw_f = _mf.get_raw_f()\n"
        f"result = raw_f(\n"
        f"    freq={freq}, eval_from='{eval_from}', market='{market}',\n"
        f"    topk={topk}, nd={nd}, phases='{phases}', require_repro_gate=True\n"
        ")\n"
        "print(json.dumps(result))\n"
    )
    proc = _sp.run([_sys.executable, "-c", _script], capture_output=True, text=True, timeout=3600)
    if proc.returncode != 0:
        raise RuntimeError(f"canonical driver subprocess failed: {proc.stderr[-500:]}")
    fresh = _json_mod.loads(proc.stdout.strip().split("\n")[-1])
    return fresh


@app.function(volumes={str(VOL_ROOT): vol}, timeout=4 * 3600)
def extend_phase_sensitivity_driver(freq: int = 20, eval_from: str = "2021-01-04",
                                     market: str = "csi1000", topk: int = 20, nd: int = 2,
                                     phases: str = "all", require_repro_gate: bool = True):
    """Cost-aware incremental phase audit.

    Reuses completed phases when context matches, computes only missing phases
    through the canonical driver, always validates phase 0.
    """
    import json as _json
    import sys as _sys

    _sys.path.insert(0, "/root")
    _sys.path.insert(0, "/root/qlib")
    from freq_experiment import retrain_phase_sensitivity_driver as _canonical_driver
    from freq_experiment import vol as _vol

    if freq < 2:
        raise ValueError("phase sensitivity requires freq >= 2")
    phase_list = sorted({int(x.strip()) for x in phases.split(",") if x.strip()})
    if not phase_list:
        raise ValueError("phases is empty")
    if phase_list[0] < 0 or phase_list[-1] >= freq:
        raise ValueError(f"phases must be within 0..{freq - 1}")

    try:
        _vol.reload()
    except Exception:
        pass

    phase_root = VOL_ROOT / "freq_phase_sensitivity"
    phase_path = phase_root / f"phase_{market}_freq{freq}.json"
    existing = _load_json(phase_path)
    baseline = existing if existing else {}

    context_ok = _same_phase_context(existing, baseline, freq=freq, eval_from=eval_from,
                                     market=market, topk=topk, nd=nd)
    if not context_ok:
        existing = None
        print("[phase-extend] existing artifact context mismatch; computing all requested phases fresh")

    existing_results = (existing or {}).get("results") or {}
    reusable = sorted(
        int(phase) for phase in phase_list
        if phase != 0 and _is_reusable_phase(existing_results.get(str(phase)))
    )
    missing = [phase for phase in phase_list if phase != 0 and phase not in reusable]

    if existing:
        phase_root.mkdir(parents=True, exist_ok=True)
        backup_path = phase_root / f"phase_{market}_freq{freq}.preextend.json"
        backup_path.write_text(_json.dumps(existing, indent=2, ensure_ascii=False))
        _vol.commit()

    run_phases = sorted(set([0] + missing))
    print(f"[phase-extend] requested={phase_list} reusable={reusable} compute={missing} validation=[0]")
    fresh = run_canonical_phase_driver.remote(
        freq=freq, eval_from=eval_from, market=market, topk=topk, nd=nd,
        phases=",".join(str(x) for x in run_phases),
    )
    try:
        _vol.reload()
    except Exception:
        pass

    if fresh.get("reproducibility") != baseline.get("reproducibility"):
        raise RuntimeError("canonical phase run no longer matches gate reproducibility manifest")

    fresh_results = fresh.get("results") or {}
    existing_results = (existing or {}).get("results") or {}
    merged_results = {}
    for phase in phase_list:
        key = str(phase)
        if phase in reusable:
            result = existing_results.get(key)
        else:
            result = fresh_results.get(key)
        if result is None:
            raise RuntimeError(f"missing requested phase result after extension: {phase}")
        merged_results[key] = _enrich_phase_result(result)

    summary = {
        field: _phase_metric_summary(merged_results, field)
        for field in ["strategy_cagr", "relative_excess_cagr", "strategy_max_drawdown",
                      "sharpe", "information_ratio"]
    }
    summary["relative_excess_cagr_range_pp"] = round(
        (summary["relative_excess_cagr"]["max"] - summary["relative_excess_cagr"]["min"]) * 100, 3,
    )

    payload = dict(fresh)
    payload.update({
        "phases": phase_list,
        "complete_phase_grid": phase_list == list(range(freq)),
        "reused_phases": reusable,
        "computed_phases": missing,
        "validation_phases": [0],
        "new_model_fits": sum(int(merged_results[str(p)]["n_retrains"]) for p in missing),
        "results": merged_results,
        "summary": summary,
    })
    phase_root.mkdir(parents=True, exist_ok=True)
    phase_path.write_text(_json.dumps(payload, indent=2, ensure_ascii=False))
    _vol.commit()
    print(f"[phase-extend] merged phases={phase_list}; reused={reusable}; computed={missing}")
    return payload


@app.local_entrypoint()
def main(freq: int = 20, eval_from: str = "2021-01-04", market: str = "csi1000",
         topk: int = 20, nd: int = 2, phases: str = "all"):
    import json
    result = extend_phase_sensitivity_driver.remote(
        freq=freq, eval_from=eval_from, market=market, topk=topk, nd=nd, phases=phases,
    )
    print(json.dumps(result.get("summary", {}), indent=2))
    print(f"reused={result.get('reused_phases')} computed={result.get('computed_phases')}")
