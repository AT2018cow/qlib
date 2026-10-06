# AGENTS.md

## Setup
- `make prerequisite` first — builds Cython exts `qlib/data/_libs/{rolling,expanding}.pyx` in-place. If `ImportError: qlib.data._libs`, rebuild it. Skipped if `*.so` exist.
- Then `pip install -e .` (dev: `pip install -e .[dev]` or `make dev` for all extras incl. `rl,lint,docs,test,analysis`).
- Python 3.8–3.12. Mac: `brew install libomp` before lightgbm/source build (`--no-binary=:all:` on macOS).

## Data (required before run/test)
- Most tests self-provision via `qlib.tests.TestAutoData.setUpClass` (`GetData().qlib_data(..., exists_skip=True)`) into `~/.qlib/qlib_data/cn_data_simple`.
- Full/local check: `python scripts/get_data.py qlib_data --name qlib_data_simple --target_dir ~/.qlib/qlib_data/cn_data --interval 1d --region cn`
- RL tests also need: `python scripts/get_data.py download_data --file_name rl_data.zip --target_dir tests/.data/rl` (Linux only; `tests/conftest.py` ignores `rl/` on non-Linux).
- Every script using `qlib.data.D` must call `qlib.init(provider_uri=..., region=...)` first. Default client: `provider_uri=~/.qlib/qlib_data/cn_data`, `region=cn`. High-freq uses dict form `{"day": ..., "1min": ...}`.

## Run / verify
- Workflow: `cd examples && qrun benchmarks/LightGBM/workflow_config_lightgbm_Alpha158.yaml` — never run from a dir containing `qlib/` (shadows package). Alt: `python qlib/cli/run.py examples/benchmarks/LightGBM/workflow_config_lightgbm_Alpha158.yaml` from repo root. Configs support Jinja env vars + `BASE_CONFIG_PATH` inheritance.
- Tests: `cd tests && python -m pytest . -m "not slow"` (markers in `tests/pytest.ini`; slow suite: `-m "slow"` needs full data, ~hours). Single file: `cd tests && python -m pytest test_workflow.py -v`. macOS flake guard: `OMP_NUM_THREADS=1 MKL_NUM_THREADS=1 NUMEXPR_NUM_THREADS=1 OPENBLAS_NUM_THREADS=1 VECLIB_MAXIMUM_THREADS=1`.
- Lint (CI order): `make black && make pylint && make flake8 && make mypy` (+ `make nbqa`, `make docs-gen` on ubuntu). Black line length is **120**: `black . -l 120 --check --diff --exclude qlib/_version.py`. Use the `make` targets — bare `pylint/flake8` use wrong flags (long `--disable=` / `--ignore=` lists live in `Makefile`).

## Architecture
- `qlib/__init__.py:init()` + `qlib/config.py:C` global config (`client`/`server` modes, `provider_uri`/`mount_path`/`region`/`exp_manager`).
- `qlib/data/` — `D` facade, `Local*Provider` + `FileStorage` (bin files) in `data.py`/`storage/`; ops in `ops.py` (`qlib.init(custom_ops=...)` to register); Cython hot paths in `data/_libs/`.
- `qlib/workflow/` — `task_train()` + `R` recorder (`MLflowExpManager`, `mlruns/`); `qlib/model/trainer.py` trains per-task.
- `qlib/backtest/` + `qlib/strategy/` + `qlib/contrib/strategy/` — executor/portfolio; `qlib/contrib/data/handler.py` = Alpha158/Alpha360 datasets.
- `qlib/model/` + `examples/benchmarks/*/` — per-model code + yaml configs; `qlib/rl/` needs `pip install -e .[rl]` (`tianshou<=0.4.10, torch, numpy<2.0`).
- `qlib/cli/run.py` (`qrun` entry, `fire`) and `scripts/get_data.py` (`fire.Fire(GetData)`).

## This fork's extension layer (actively maintained)
- **Read `docs/experiments/14-pre-tuner-stage-summary-20261007.md` first.** It is the current authority for research conclusions, pool status, superseded historical claims, and next-stage work. Use `12-pre-tuner-audit.md` / `13-handoff-after-pr8-20261006.md` for deeper audit history. `HANDOVER.md`, `05-final-audit.md`, and older Batch A/B/C conclusions are historical unless explicitly revalidated by doc 14.
- **Current pool status (2026-10-07)**: CSI1000 = reproducibility PASS + positive corrected baseline, primary tuner line; ChiNext = reproducibility PASS but frozen top20/nd3 0/5/10/15 screen has negative relative CAGR in all sampled phases, bounded-rescue only; STAR = reproducibility gate FAIL at prediction-chunk equality, alpha conclusion unavailable until repaired. Do not describe STAR as proven unprofitable, and do not interpret phase number as model age.
- **Current research protocol**: T-close scoring -> T+1-open execution, continuous account, board/date-aware CNY tick limits, no 5% high-open overlay, canonical geometric portfolio metrics, deterministic LightGBM gate before phase/tuning work.
- **Board-aware universe / satellite plumbing**: `board_rules.py` provides STAR/ChiNext board rules, equal-weight benchmark construction, custom pools, and board-aware limit filtering. The older Batch A/B/C profitability statements and "STAR closed" wording are historical; current corrected pool evidence is in doc 14. Dual-pool signal publication may remain operational, but publication is not evidence that ChiNext is currently approved for production capital.
- `modal_qlib_cn_a10g.py` — all Modal functions (production daily cron + research batches). Deployment: `modal deploy modal_qlib_cn_a10g.py` (at2018cow workspace; `github-push` secret required for cron push).
- **Production is live**: `--best --daily` = csi1000+top20/nd2 candidate, cron at 07:00 CST weekdays (dual-pool: csi1000 then chinext, see board-aware universe below), auto-pushes signals to `results/signals/` via GitHub API; `daily_standalone`/`daily_cron` are `nonpreemptible=True`.
- `qlib_audit_fixes.py` / `qlib_live_retrain.py` — audited label-maturity/purge boundary math + 20-session retrain cache policy (both unit-tested in `tests/test_qlib_*.py`; do NOT modify without re-running them).
- `freq_experiment.py` — standalone retraining-frequency experiment (no Secret deps, runs in any workspace).
- **Data in this fork**: chenditc daily full release (append-only, no revisions, real historical constituents — all verified); Volume `qlib-cn-data`. Docker-style local data setup from upstream README section does NOT apply to the daily pipeline (it always downloads fresh).
- **Critical bug-fix conventions** (hard-won, see 03-risks-and-audit.md): limit-up filter must use close/prev-close with correct MultiIndex alignment; train/valid/test boundaries must be purged via `purge_cfg_splits`; never trust a too-good backtest number before a look-ahead audit.

## Modal usage (critical, repeatedly forgotten)
- **NEVER use `nohup ... &` for Modal commands** — the shell tool kills background processes when its timeout expires, silently losing the run. Use synchronous execution with a long `timeout` (e.g. `timeout: 3600000` for 1 hour).
- **`modal run` creates a temporary app** — it uses the local file directly; no redeploy needed for experiments. Use `modal deploy` only for the production cron.
- **Workspace switching**: `modal profile activate <name>` — always verify with `modal profile current` after switching. at2018cow = production; infi = experiments.
- **`modal run` is synchronous** — it waits for all functions to complete. For long jobs, set the bash `timeout` parameter generously (Batch C ≈ 20 min → `timeout: 1200000`; freq experiments ≈ 60 min → `timeout: 3600000`).
- **`modal run --detach` is dangerous** — disconnecting cancels pending inputs (confirmed: two detach runs both lost work after ~10 min). Only use for fire-and-forget one-shot scripts.
- **Image rebuild**: `modal run` rebuilds the image from the local file each time (add_local_dir + run_commands). Changes to helper modules (board_rules.py, board_execution.py, etc.) are picked up automatically. No need to redeploy for experiments.
- **Volume data is per-workspace** — building a benchmark or pool file on infi does NOT make it available on at2018cow. Run data prep on the workspace you'll use.

## Path discipline (repeated errors, fix permanently)
- **Working directory is `/home/ss/git_repos/qlib`** — always use `workdir` parameter; never `cd` into a subdirectory you're already in (e.g. being in `tests/` and running `cd tests` again).
- **`read` tool takes a file path, NOT bash syntax** — don't pipe or add shell commands to the path.
- **Never typo the path** — it's `git_repos`, not `git_reos`.
- **Run tests from the repo root with `PYTHONPATH=/home/ss/git_repos/qlib`**, or from `tests/` with the same PYTHONPATH; use the venv at `/home/ss/git_repos/qlib/.venv/bin/python`.

## Conventions / gotchas
- Docstrings: Numpydoc style (`docs/developer/code_standard_and_dev_guide.rst`).
- `make clean` deletes `*.so/*.cpp/mlruns/build/dist`; use it before rebuilds, not casually.
- `qlib/_version.py` is generated by `setuptools_scm` — excluded from black, never edit.
- Known quirk: set `group_key=False` in `groupby` for pandas 1.5→2.0 compat.
- Logging via `qlib/log.py:get_module_logger`; don't `disable_existing_loggers`.
