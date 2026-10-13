"""Check the real pyqlib wheel build without .git, without Modal or network.

Run with the *same* installed build toolchain that the Modal image uses:
    python audit/csi1000_p0_scmless_build_check.py

This explicitly invokes pip's PEP 517 metadata+wheel build; it is not
a static string test. Fails closed if required build dependencies are absent.
Never rewrites the git checkout or creates cloud tasks.
"""
from __future__ import annotations

import argparse
import ast
import importlib.util
import json
import os
import shutil
import subprocess
import sys
import tempfile
import tomllib
import zipfile
from pathlib import Path


def p0_image_version_env(modal_file: Path) -> dict[str, str]:
    """Extract the exact fixed image env from its parsed AST."""
    root = ast.parse(modal_file.read_text(encoding="utf-8"))
    for node in ast.walk(root):
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute) and node.func.attr == "env":
            if len(node.args) != 1:
                continue
            try:
                value = ast.literal_eval(node.args[0])
            except (ValueError, TypeError):
                continue
            if isinstance(value, dict) and "SETUPTOOLS_SCM_PRETEND_VERSION_FOR_PYQLIB" in value:
                return value
    raise RuntimeError("STOP_IMAGE: P0 image has no explicit pyqlib SCM version override")


def check_build(root: Path) -> dict[str, str]:
    repo = root.resolve()
    project = tomllib.loads((repo / "pyproject.toml").read_text(encoding="utf-8"))
    if project["project"]["name"] != "pyqlib":
        raise RuntimeError("STOP_IMAGE: package identity is not pyqlib")
    if "version" not in project["project"]["dynamic"]:
        raise RuntimeError("STOP_IMAGE: project no longer declares dynamic version")
    image_env = p0_image_version_env(repo / "audit/csi1000_layered_search_modal.py")
    expected = image_env["SETUPTOOLS_SCM_PRETEND_VERSION_FOR_PYQLIB"]
    if image_env.get("VCS_VERSIONING_PRETEND_VERSION_FOR_PYQLIB") != expected:
        raise RuntimeError("STOP_IMAGE: setuptools-scm and vcs-versioning overrides differ")
    if not expected or not expected[0].isdigit():
        raise RuntimeError("STOP_IMAGE: invalid pretend version")
    for package in ("setuptools", "setuptools_scm", "Cython", "numpy", "wheel"):
        if importlib.util.find_spec(package) is None:
            raise RuntimeError(
                f"STOP_IMAGE: {package} not installed. Re-run in an isolated "
                "environment with the Modal image build-toolchain versions."
            )
    with tempfile.TemporaryDirectory(prefix="csi1000-p0-scmless-") as temp:
        scratch = Path(temp)
        source = scratch / "source"
        source.mkdir()
        for name in ("README.md", "pyproject.toml", "setup.py"):
            shutil.copy2(repo / name, source / name)
        for name in ("LICENSE", "MANIFEST.in", "setup.cfg"):
            if (repo / name).is_file():
                shutil.copy2(repo / name, source / name)
        shutil.copytree(
            repo / "qlib", source / "qlib",
            ignore=shutil.ignore_patterns(
                ".git", "__pycache__", "*.pyc", "*.so", "*.cpp",
                "_version.py", "*.egg-info",
            ),
        )
        if (source / ".git").exists() or (source / "qlib/_version.py").exists():
            raise RuntimeError("STOP_IMAGE: source unexpectedly contains SCM or prebuilt version")
        wheels = scratch / "dist"
        wheels.mkdir()
        env = os.environ.copy()
        env.update(image_env)
        # Explicitly prohibit network access and stale outside SCM overrides.
        env["PIP_NO_INDEX"] = "1"
        env["PIP_DISABLE_PIP_VERSION_CHECK"] = "1"
        env.pop("SETUPTOOLS_SCM_PRETEND_VERSION", None)
        env.pop("VCS_VERSIONING_PRETEND_VERSION", None)
        env.pop("PYTHONPATH", None)
        cmd = [
            sys.executable, "-m", "pip", "wheel", ".", "--no-deps",
            "--no-build-isolation", "--no-index", "--wheel-dir", str(wheels),
        ]
        run = subprocess.run(
            cmd, cwd=source, env=env,
            text=True, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
        )
        if run.returncode:
            raise RuntimeError(
                f"STOP_IMAGE: Git-less pyqlib wheel build failed "
                f"(exit={run.returncode}):\n{run.stdout[-12000:]}"
            )
        built = sorted(wheels.glob("pyqlib-*.whl"))
        if len(built) != 1:
            raise RuntimeError(f"STOP_IMAGE: expected exactly one wheel, found {len(built)}")
        with zipfile.ZipFile(built[0]) as wheel:
            version_files = [x for x in wheel.namelist() if x == "qlib/_version.py"]
            if len(version_files) != 1:
                raise RuntimeError("STOP_IMAGE: installed wheel lacks qlib/_version.py")
            meta_files = [x for x in wheel.namelist() if x.endswith(".dist-info/METADATA")]
            if len(meta_files) != 1:
                raise RuntimeError("STOP_IMAGE: wheel metadata missing or ambiguous")
            metadata = wheel.read(meta_files[0]).decode("utf-8")
            if f"Version: {expected}" not in metadata.splitlines():
                raise RuntimeError(f"STOP_IMAGE: wheel metadata does not report {expected}")
        return {
            "status": "GITLESS_WHEEL_BUILD_PASS",
            "package": "pyqlib",
            "version": expected,
            "wheel": built[0].name,
            "source_contains_git": "false",
            "no_network": "true",
            "modal_remote_run": "false",
        }


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Actual non-network pyqlib wheel build without .git (not a Modal run)"
    )
    parser.add_argument(
        "--repo-root", type=Path, default=Path(__file__).resolve().parents[1]
    )
    args = parser.parse_args()
    try:
        print(json.dumps(check_build(args.repo_root), indent=2), flush=True)
        return 0
    except Exception as exc:
        print(f"{type(exc).__name__}: {exc}", file=sys.stderr, flush=True)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
