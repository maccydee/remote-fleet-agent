"""Explicit native opt-in; no discovery, elevation or automatic approvals."""
from __future__ import annotations

import importlib.util
import os
from pathlib import Path

import pytest


def pytest_addoption(parser):
    parser.addoption("--run-native", action="store_true", default=False,
                     help="Allow explicitly manifested enrolled SSH native tests")


def pytest_configure(config):
    config.addinivalue_line("markers", "native: requires explicit manifest and enrolled native endpoint")


def pytest_collection_modifyitems(config, items):
    for item in items:
        if "native" in item.keywords and not config.getoption("--run-native"):
            item.add_marker(pytest.mark.skip(reason="NOT_RUN: use --run-native and a private native manifest"))


@pytest.fixture
def native_manifest(request):
    if not request.config.getoption("--run-native"):
        pytest.skip("NOT_RUN: native flag absent")
    path = os.environ.get("REMOTE_FLEET_NATIVE_MANIFEST")
    if not path:
        pytest.skip("BLOCKED: REMOTE_FLEET_NATIVE_MANIFEST is missing; no endpoint contacted")
    spec = importlib.util.spec_from_file_location("validate_support_native", Path(__file__).parents[2] / "scripts" / "validate-support.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    try:
        manifest = module.load_manifest(Path(path))
    except (OSError, ValueError) as exc:
        pytest.fail("Native manifest preflight rejected: " + str(exc))
    return module, manifest


@pytest.fixture
def native_lab_factory(native_manifest, tmp_path):
    module, manifest = native_manifest

    def create(platform):
        targets = [t for t in manifest["targets"] if t["platform"] == platform]
        if not targets:
            pytest.skip("BLOCKED: current " + platform + " native target absent")
        if not manifest.get("workspace_url") or not manifest.get("operator_token_file"):
            pytest.skip("BLOCKED: native workflow needs live controller and technician token reference")
        return module.NativeLab(manifest, targets[0], output=tmp_path,
                                approval_wait_seconds=manifest.get("approval_wait_seconds", 0))

    return module, create
