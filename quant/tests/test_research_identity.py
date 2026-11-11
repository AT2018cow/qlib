"""Research acceptance rejects changed data and indirect implementations."""

import json
import copy
from pathlib import Path
import sys
from types import SimpleNamespace

import pytest
import pandas as pd

import research_identity as identity


RESEARCH = Path(__file__).resolve().parents[1] / "code/research/regime_reselection"
sys.path.insert(0, str(RESEARCH))
import prelive_local


@pytest.fixture
def frozen_provider(tmp_path):
    provider = tmp_path / "provider"
    files = {
        "calendars/day.txt": "2018-01-02\n2018-01-03\n2026-09-30\n",
        "instruments/all.txt": "SH600000\t1999-01-01\t2026-09-30\n",
        "features/sh600000/close.day.bin": "original close bytes",
    }
    for name, text in files.items():
        p = provider / name
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(text)
    hashes = {name: identity.file_sha(provider / name) for name in files}
    manifest = tmp_path / "provider.json"
    manifest.write_text(json.dumps({"files": hashes, "calendar_end": "2026-09-30"}))
    register = {
        "provider_identity": {
            "files_manifest_sha256": identity.content_sha(hashes),
            "calendar_end": "2026-09-30",
            "file_count": len(hashes),
        }
    }
    return provider, manifest, register


def test_provider_rejects_short_calendar_and_changed_quotes(frozen_provider):
    provider, manifest, register = frozen_provider
    contract = identity.provider_contract(register, manifest)
    assert identity.verify_provider(provider, contract) == contract["sha256"]
    calendar = provider / "calendars/day.txt"
    original = calendar.read_bytes()
    calendar.write_text("2018-01-02\n2018-01-03\n")
    with pytest.raises(ValueError, match="calendar"):
        identity.verify_provider(provider, contract)
    calendar.write_bytes(original)
    (provider / "features/sh600000/close.day.bin").write_text("revised quote")
    with pytest.raises(ValueError, match="file differs"):
        identity.verify_provider(provider, contract)


def test_build_jobs_blocks_unregistered_provider_before_any_replay(frozen_provider, tmp_path):
    provider, manifest, register = frozen_provider
    (provider / "calendars/day.txt").write_text("2018-01-02\n2018-01-03\n")
    registration = tmp_path / "registration.json"
    registration.write_text(json.dumps(register))
    args = SimpleNamespace(
        register=registration, provider=provider, provider_manifest=manifest, provider_amendment=None
    )
    with pytest.raises(ValueError, match="calendar"):
        prelive_local.build_jobs(args, register)


def test_only_registered_amendment_can_replace_a_specific_file(frozen_provider, tmp_path):
    provider, manifest, register = frozen_provider
    name = "features/sh600000/close.day.bin"
    path = provider / name
    before = identity.file_sha(path)
    path.write_text("official correction")
    amendment = tmp_path / "amendment.json"
    amendment.write_text(
        json.dumps(
            {
                "schema_version": 1,
                "base_files_manifest_sha256": register["provider_identity"]["files_manifest_sha256"],
                "replacements": {name: {"before_sha256": before, "after_sha256": identity.file_sha(path)}},
            }
        )
    )
    with pytest.raises(ValueError, match="not explicitly registered"):
        identity.provider_contract(register, manifest, amendment)
    register["provider_identity"]["allowed_amendment_sha256"] = [identity.file_sha(amendment)]
    amended = identity.provider_contract(register, manifest, amendment)
    assert identity.verify_provider(provider, amended) == amended["sha256"]
    assert amended["sha256"] != identity.provider_contract(register, manifest)["sha256"]
    amendment.write_text(amendment.read_text() + "\n")
    with pytest.raises(ValueError, match="not explicitly registered"):
        identity.provider_contract(register, manifest, amendment)


@pytest.fixture
def executable_tree(tmp_path):
    root = tmp_path / "checkout"
    helper = root / "quant/code/execution/qlib_live_retrain.py"
    helper.parent.mkdir(parents=True)
    helper.write_text("def configure_asof(): return 'original'\n")
    driver = root / "quant/code/driver.py"
    driver.write_text("from execution.qlib_live_retrain import configure_asof\n")
    yaml = root / "examples/benchmarks/LightGBM/workflow_config_lightgbm_Alpha158.yaml"
    yaml.parent.mkdir(parents=True)
    yaml.write_text("model: fixed\n")
    return root, helper, driver


def test_shared_helper_change_invalidates_source_smoke_and_fit_cache(executable_tree):
    root, helper, driver = executable_tree
    original_driver = identity.file_sha(driver)
    implementation = identity.source_manifest("training", repo_root=root)
    job = {"source_manifest": implementation, "asset": {"digest": "sha256:fixture"}, "first_execution": "2026-10-08"}
    smoke = {
        "status": "SMOKE_PASS",
        "source_identity_version": identity.SOURCE_IDENTITY_VERSION,
        "source_sha256": implementation["sha256"],
        "source_manifest": implementation,
        "asset_digest": job["asset"]["digest"],
        "first_execution": job["first_execution"],
    }
    block = {"fit_asof": "2026-09-30", "signal_end": "2026-10-23"}
    receipt = {
        "status": "PASS",
        "source_identity_version": identity.SOURCE_IDENTITY_VERSION,
        "source_manifest_sha256": implementation["sha256"],
        "asset_digest": job["asset"]["digest"],
        **block,
        "profile": "fixed",
        "prediction_sha256": "prediction",
    }
    assert identity.matching_training_smoke(smoke, job)
    assert identity.matching_fit_receipt(receipt, job, block, "fixed", "prediction")
    helper.write_text("def configure_asof(): return 'corrected'\n")
    assert identity.file_sha(driver) == original_driver
    with pytest.raises(ValueError, match="source manifest"):
        identity.verify_source_manifest(implementation, "training", repo_root=root)
    new_job = {**job, "source_manifest": identity.source_manifest("training", repo_root=root)}
    assert not identity.matching_training_smoke(smoke, new_job)
    assert not identity.matching_fit_receipt(receipt, new_job, block, "fixed", "prediction")
    assert not identity.matching_training_smoke({**smoke, "source_identity_version": 1}, job)
    assert not identity.matching_fit_receipt(
        {**receipt, "source_identity_version": 1}, job, block, "fixed", "prediction"
    )


def test_training_yaml_and_new_dependency_invalidate_source(executable_tree):
    root, helper, driver = executable_tree
    original = identity.source_manifest("training", repo_root=root)
    yaml = root / "examples/benchmarks/LightGBM/workflow_config_lightgbm_Alpha158.yaml"
    yaml.write_text("model: changed\n")
    with pytest.raises(ValueError, match="source manifest"):
        identity.verify_source_manifest(original, "training", repo_root=root)
    replay = identity.source_manifest("replay", repo_root=root)
    (helper.parent / "new_dependency.py").write_text("quantity_rule = 'changed'\n")
    with pytest.raises(ValueError, match="source manifest"):
        identity.verify_source_manifest(replay, "replay", repo_root=root)


def test_cached_prepared_data_is_rechecked_before_reuse(frozen_provider, executable_tree, tmp_path, monkeypatch):
    provider, manifest, register = frozen_provider
    code_root, helper, driver = executable_tree
    monkeypatch.setattr(identity, "REPO_ROOT", code_root)
    implementation = identity.source_manifest("replay")
    contract = identity.provider_contract(register, manifest)
    signal = tmp_path / "signal.parquet"
    signal.write_text("frozen prediction fixture")
    job = {
        "output_root": str(tmp_path / "runs"),
        "run_id": "original",
        "key": "candidate",
        "signals": [str(signal)],
        "expected_signal_sha256": [identity.file_sha(signal)],
        "source_manifest": implementation,
        "engine_sha256": implementation["sha256"],
        "provider": str(provider),
        "provider_contract": contract,
        "candidate": {"id": "candidate", "market": "csi300", "topk": 1, "dropn": 1},
        "start": "2018-01-02",
        "end": "2018-01-03",
        "slippage": [0],
        "account": 1_000_000,
        "register_sha256": "registered",
    }
    key = (
        job["provider"],
        contract["sha256"],
        job["engine_sha256"],
        tuple(job["expected_signal_sha256"]),
        "csi300",
        job["start"],
        job["end"],
    )
    days = [job["start"], job["end"]]
    data = {
        "provenance": {
            "provider_metadata": {"calendars/day.txt": contract["files"]["calendars/day.txt"]},
            "quote_files": {},
        },
        "days": days,
        "calendar": days,
        "signal": pd.Series(
            1.0,
            index=pd.MultiIndex.from_product([pd.to_datetime(days), ["SH600000"]], names=["datetime", "instrument"]),
        ),
        "quotes": {
            days[1]: {
                field: {"SH600000": value}
                for field, value in (
                    ("$open", 10),
                    ("$close", 11),
                    ("$factor", 1),
                    ("Ref($close,1)", 10),
                    ("$volume", 1_000_000),
                )
            }
        },
        "bench": pd.Series([0.0], index=pd.to_datetime(days[1:])),
        "listing": {},
    }
    monkeypatch.setattr(prelive_local, "_DATA_CACHE", (key, data))
    # No Qlib data access is required for the reuse path.
    receipt = prelive_local.execute(job)
    assert receipt["prepared_data_reused"] is True
    (provider / "features/sh600000/close.day.bin").write_text("changed under the same path")
    with pytest.raises(ValueError, match="file differs"):
        prelive_local.execute({**job, "run_id": "changed_provider"})
    (provider / "features/sh600000/close.day.bin").write_text("original close bytes")
    helper.write_text("def configure_asof(): return 'corrected'\n")
    with pytest.raises(ValueError, match="source manifest"):
        prelive_local.execute({**job, "run_id": "changed_implementation"})


def test_prepared_inputs_cannot_read_outside_registered_file_set(frozen_provider):
    provider, manifest, register = frozen_provider
    contract = identity.provider_contract(register, manifest)
    with pytest.raises(ValueError, match="unregistered"):
        identity.verify_prepared_provenance(
            {"provider_metadata": {}, "quote_files": {"SH999999/close": "unknown"}}, contract
        )


def test_real_modal_worker_rejects_stale_sources_before_provider_access(monkeypatch):
    import october_backfill_modal

    def unexpected_download(asset):
        pytest.fail("stale implementations must be rejected before data download or paid training")

    monkeypatch.setattr(october_backfill_modal, "download_provider", unexpected_download)
    with pytest.warns(UserWarning, match="executing locally"):
        with pytest.raises(ValueError, match="source manifest"):
            october_backfill_modal.build_market.local({"source_manifest": {"sha256": "stale"}})


def test_replay_smoke_cannot_cross_provider_or_implementation_versions():
    implementation = {
        "sha256": "source",
        "schema_version": identity.SOURCE_IDENTITY_VERSION,
        "files": {"helper.py": "digest"},
    }
    job = {
        "source_manifest": implementation,
        "engine_sha256": "source",
        "provider_contract": {"sha256": "provider"},
        "register_sha256": "registration",
    }
    row = {
        "status": "COMPLETED",
        "source_identity_version": identity.SOURCE_IDENTITY_VERSION,
        "source_manifest": implementation,
        "engine_sha256": "source",
        "provider_contract_sha256": "provider",
        "register_sha256": "registration",
    }
    smoke = {
        "status": "COMPLETED",
        "jobs": [
            {
                "key": market,
                "candidate": {"market": market},
                "engine_sha256": "source",
                "register_sha256": "registration",
            }
            for market in ("csi300", "csi500", "csi1000")
        ],
        "results": [{**copy.deepcopy(row), "key": market} for market in ("csi300", "csi500", "csi1000")],
    }
    assert prelive_local.matching_replay_smoke(smoke, job)
    for field, changed in (
        ("provider_contract_sha256", "other"),
        ("source_identity_version", 1),
        ("source_manifest", {}),
    ):
        modified = copy.deepcopy(smoke)
        modified["results"][0][field] = changed
        assert not prelive_local.matching_replay_smoke(modified, job)


def _write_identity_tree(root, files):
    for name, text in files.items():
        path = root / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text)


def test_release_identity_changes_for_resources_but_semantic_does_not(executable_tree):
    root, helper, driver = executable_tree
    _write_identity_tree(
        root,
        {
            "quant/code/production/website_replay_core.py": "def replay(): return 1\n",
            "quant/code/production/website_simulation.py": "def series(): return 1\n",
            "quant/code/ops/website_simulation_modal.py": "cpu = 8\n",
        },
    )
    dependencies = ("quant/code/production/website_replay_core.py", "quant/code/production/website_simulation.py")
    release = identity.release_manifest(repo_root=root)
    semantic = identity.website_semantic_manifest(repo_root=root, dependencies=dependencies)
    account = identity.account_contract_manifest(
        {"market": "csi300", "profile": "p", "topk": 10, "dropn": 1, "open_cost": 0.0005, "close_cost": 0.0015}
    )
    (root / "quant/code/ops/website_simulation_modal.py").write_text("cpu = 4\nmemory = 12288\n")
    assert identity.release_manifest(repo_root=root)["sha256"] != release["sha256"]
    assert identity.website_semantic_manifest(repo_root=root, dependencies=dependencies)["sha256"] == semantic["sha256"]
    assert (
        identity.account_contract_manifest(
            {"market": "csi300", "profile": "p", "topk": 10, "dropn": 1, "open_cost": 0.0005, "close_cost": 0.0015}
        )
        == account
    )
    (root / "quant/code/production/website_replay_core.py").write_text("def replay(): return 2\n")
    assert identity.website_semantic_manifest(repo_root=root, dependencies=dependencies)["sha256"] != semantic["sha256"]


def test_account_contract_changes_on_fee_or_topk(executable_tree):
    root, helper, driver = executable_tree
    base = {"market": "csi300", "profile": "p", "topk": 10, "dropn": 1, "open_cost": 0.0005, "close_cost": 0.0015}
    original = identity.account_contract_manifest(base)
    assert identity.account_contract_manifest(dict(base)) == original
    for key, value in (("topk", 20), ("open_cost", 0.001), ("close_cost", 0.002)):
        changed = identity.account_contract_manifest({**base, key: value})
        assert changed["sha256"] != original["sha256"], key
    with pytest.raises(ValueError, match="missing account contract"):
        identity.account_contract_manifest({"market": "csi300"})


def test_loaded_semantic_closure_rejects_unregistered_business_module(executable_tree):
    root, helper, driver = executable_tree
    dependencies = ("quant/code/driver.py", "quant/code/execution/qlib_live_retrain.py")
    identity.verify_loaded_semantic_closure(
        [root / "quant/code/driver.py", root / "quant/code/execution/qlib_live_retrain.py"],
        repo_root=root,
        dependencies=dependencies,
    )
    with pytest.raises(ValueError, match="unregistered website semantic dependency"):
        identity.verify_loaded_semantic_closure(
            [root / "quant/code/new_business.py"],
            repo_root=root,
            dependencies=dependencies,
        )
    with pytest.raises(ValueError, match="unregistered website semantic dependency"):
        identity.verify_loaded_semantic_closure(
            ["/etc/passwd"],
            repo_root=root,
            dependencies=dependencies,
        )


def test_website_semantic_dependency_table_covers_the_replay_closure():
    import ast

    root = Path(__file__).resolve().parents[2]
    code_root = root / "quant/code"
    declared = set(identity.WEBSITE_SEMANTIC_DEPENDENCIES)
    by_module = {}
    for path in code_root.rglob("*.py"):
        by_module[path.stem] = path.relative_to(root).as_posix()
    pending = [
        "quant/code/production/website_replay_core.py",
        "quant/code/production/website_simulation.py",
        "quant/code/production/qlib_runtime.py",
    ]
    seen = set()
    missing = []
    while pending:
        name = pending.pop()
        if name in seen:
            continue
        seen.add(name)
        if name not in declared:
            missing.append(name)
        tree = ast.parse((root / name).read_text())
        for node in ast.walk(tree):
            module = None
            if isinstance(node, ast.ImportFrom) and node.level == 0:
                module = node.module
            elif isinstance(node, ast.Import):
                module = node.names[0].name
            if not module:
                continue
            target = by_module.get(module.split(".")[0])
            if target and target not in seen:
                pending.append(target)
    assert not missing, f"website semantic manifest is missing imports: {sorted(missing)}"
    for name in declared:
        assert (root / name).is_file(), name
