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
- **Read the task-specific handoff first**: doc 22 (`docs/experiments/22-handoff-star-chinext-research-20261008.md`) for STAR/ChiNext model research; doc 23 (`docs/experiments/23-web-ui-handoff-20261008.md`) for website work; or doc 24 (`docs/experiments/24-csi1000-independent-sharpe-audit-handoff-20261009.md`) for independent CSI1000 Sharpe/account/execution integrity audit. The Sharpe audit is PENDING, not an authorization to retune. Doc 21 remains historical combined context; docs 16–20 are frozen CSI1000 Stage-A/B lineage. Do not cross-edit unrelated workstreams or reuse the consumed Stage-B tail as fresh OOS.
- **CSI1000 is frozen**: Stage-B winner is the sole canonical production profile. Do not reopen Stage-A/B selection or use the consumed 2025-01-02..2026-09-30 confirmation tail for post-hoc tuning unless a concrete implementation/data-integrity defect is found.
- **ChiNext is paused in production**: research code/history remain, but scheduled daily publication and the public website entry are disabled until a new model passes a separately versioned validation gate.
- **STAR is not rejected**: its corrected prediction reproducibility gate has not passed, so alpha is unknown. Repair reproducibility/data lineage before interpreting profitability.
- **Current execution contract**: T-close scoring -> T+1-open execution, continuous account, Top20/Drop2 for CSI1000, board/date-aware CNY tick limits, no 5% high-open overlay, canonical geometric portfolio metrics, deterministic LightGBM.
- `modal_qlib_cn_a10g.py` contains the production daily path and historical research helpers. Current scheduled production publishes CSI1000 only.
- `qlib_audit_fixes.py` / `qlib_live_retrain.py` contain audited label-maturity/purge and 20-session retrain logic. Re-run their tests after any change.
- `board_rules.py` / `board_execution.py` contain STAR/ChiNext board-aware rules and universe utilities. Preserve date-sensitive limit rules and listing-day exemptions.
- Public-repository rule: do not add access credentials, private workspace/profile names, account details, personal filesystem paths, or other unnecessary identity information to docs, logs, fixtures, or examples.

## Modal usage
- Use an authorized local Modal profile; repository documentation intentionally does not name private workspaces or credential objects.
- `modal run` is for temporary research runs; `modal deploy modal_qlib_cn_a10g.py` updates the scheduled production app.
- Prefer synchronous runs with adequate timeout. Do not rely on shell-backgrounded `nohup` jobs for long experiments.
- Workspace-scoped volumes are isolated. Prepare data/benchmarks in the same authorized workspace that will execute the experiment.
- Do not print, paste, or commit deployment credentials. Configure them through the platform's secret-management UI/CLI outside the repository.

## Path discipline
- Do not encode a developer-specific checkout path in scripts or docs. Resolve the repository root from the current checkout or tool workdir.
- Run project tests from the repository root, or set `PYTHONPATH` to the current checkout dynamically.
- Tool file-read APIs take file paths, not shell pipelines.
## Conventions / gotchas
- Docstrings: Numpydoc style (`docs/developer/code_standard_and_dev_guide.rst`).
- `make clean` deletes `*.so/*.cpp/mlruns/build/dist`; use it before rebuilds, not casually.
- `qlib/_version.py` is generated by `setuptools_scm` — excluded from black, never edit.
- Known quirk: set `group_key=False` in `groupby` for pandas 1.5→2.0 compat.
- Logging via `qlib/log.py:get_module_logger`; don't `disable_existing_loggers`.
