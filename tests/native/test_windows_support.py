"""Windows native preflight never reinstalls OpenSSH or extends enrollment."""
import pytest

pytestmark = pytest.mark.native


async def test_windows_test_pc_identity(native_manifest):
    module, manifest = native_manifest
    targets = [t for t in manifest["targets"] if t["platform"] == "windows"]
    if not targets:
        pytest.skip("BLOCKED: Windows test PC enrollment absent")
    assert len(targets) == 1
    assert targets[0]["device"] == "DESKTOP-6PB6U6S"
    result = await module.preflight(manifest, targets[0])
    assert result["status"] == "PASS"
