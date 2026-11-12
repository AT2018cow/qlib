"""R07: image builds must never include local secrets or credentials."""

from __future__ import annotations

from pathlib import Path

import pytest

from image_filters import should_exclude_from_image


@pytest.mark.parametrize(
    "path",
    [
        ".env",
        ".env.local",
        "nested/.env.production",
        "deploy.key",
        "certs/server.pem",
        "certs/server.crt",
        ".ssh/id_rsa",
        "home/id_ed25519",
        "secrets/provider.json",
        "credentials/account.json",
        ".netrc",
        ".npmrc",
        ".pypirc",
        ".aws/credentials",
        ".gnupg/private-keys-v1.d/key",
        "quant/data/signals/2026-11-11_top20.csv",
        ".venv/lib/python3.11/site-packages/akshare.py",
        "qlib/data/_libs/rolling.cpp",
    ],
)
def test_sensitive_and_generated_paths_are_excluded(path):
    assert should_exclude_from_image(path) is True
    assert should_exclude_from_image("quant/data/strategy_releases/csi300.json") is False
    assert should_exclude_from_image("quant/data") is False


@pytest.mark.parametrize(
    "path",
    [
        "quant/code/production/provider_build.py",
        "quant/code/data_ingest/store.py",
        "examples/benchmarks/LightGBM/workflow_config_lightgbm_Alpha158.yaml",
        "quant/docs/07-code-review/2026-11-11-overlay-primary-review-remediation.md",
        "AGENTS.md",
    ],
)
def test_business_code_and_configuration_remain(path):
    assert should_exclude_from_image(path) is False


def test_nested_temporary_paths_are_filtered(tmp_path):
    sensitive = tmp_path / "repo" / ".env.local"
    sensitive.parent.mkdir(parents=True)
    sensitive.write_text("placeholder")  # never read by the predicate
    business = tmp_path / "repo" / "quant" / "code" / "production" / "provider_build.py"
    business.parent.mkdir(parents=True)
    business.write_text("pass")
    assert should_exclude_from_image(sensitive) is True
    assert should_exclude_from_image(business) is False


def test_both_image_builders_use_the_shared_filter():
    from pathlib import Path

    root = Path(__file__).resolve().parents[2]
    for relative in (
        "quant/code/production/modal_qlib_cn_a10g.py",
        "quant/code/ops/overlay_primary_probe.py",
    ):
        source = (root / relative).read_text()
        assert "should_exclude_from_image" in source, relative


def test_modal_entrypoint_resolver_supports_shallow_serialized_layout(tmp_path):
    """S01: ``/root/<entry>.py`` must not index a nonexistent parent."""
    import ast

    root = Path(__file__).resolve().parents[2]
    source = (root / "quant/code/production/modal_qlib_cn_a10g.py").read_text()
    tree = ast.parse(source)
    resolver = next(
        (node for node in tree.body if isinstance(node, ast.FunctionDef) and node.name == "_resolve_quant_code_root"),
        None,
    )
    assert resolver is not None, "production entrypoint must expose a testable, bounded path resolver"
    namespace = {"Path": Path}
    exec(compile(ast.Module(body=[resolver], type_ignores=[]), "<entrypoint-resolver>", "exec"), namespace)

    container_code = tmp_path / "qlib" / "quant" / "code"
    container_code.mkdir(parents=True)
    (container_code / "quant_bootstrap.py").write_text("# marker\n")
    shallow_entry = tmp_path / "modal_qlib_cn_a10g.py"
    shallow_entry.write_text("# serialized entrypoint\n")
    assert namespace["_resolve_quant_code_root"](shallow_entry, container_code) == container_code


def test_modal_entrypoint_resolver_supports_checkout_and_fails_clearly(tmp_path):
    """S01: local checkout discovery is ancestor-based and fail-closed."""
    import ast

    root = Path(__file__).resolve().parents[2]
    source = (root / "quant/code/production/modal_qlib_cn_a10g.py").read_text()
    resolver = next(
        node
        for node in ast.parse(source).body
        if isinstance(node, ast.FunctionDef) and node.name == "_resolve_quant_code_root"
    )
    namespace = {"Path": Path}
    exec(compile(ast.Module(body=[resolver], type_ignores=[]), "<entrypoint-resolver>", "exec"), namespace)

    checkout_code = tmp_path / "repo" / "quant" / "code"
    entry = checkout_code / "production" / "modal_qlib_cn_a10g.py"
    entry.parent.mkdir(parents=True)
    entry.write_text("# local entrypoint\n")
    (checkout_code / "quant_bootstrap.py").write_text("# marker\n")
    missing_container = tmp_path / "missing" / "quant" / "code"
    assert namespace["_resolve_quant_code_root"](entry, missing_container) == checkout_code

    with pytest.raises(RuntimeError, match="quant code root"):
        namespace["_resolve_quant_code_root"](tmp_path / "shallow.py", missing_container)
