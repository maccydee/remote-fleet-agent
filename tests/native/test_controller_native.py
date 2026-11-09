"""Controller-host proof must be collected on the actual named host."""
import platform
import sys

import pytest

pytestmark = pytest.mark.native


def test_current_controller_interpreter(native_manifest):
    _, manifest = native_manifest
    expected = manifest.get("controller_platform")
    actual = {"Darwin": "macos", "Linux": "linux", "Windows": "windows"}.get(platform.system())
    if not expected:
        pytest.skip("NOT_RUN: controller_platform not explicitly declared")
    assert actual == expected
    assert sys.version_info >= (3, 11)
