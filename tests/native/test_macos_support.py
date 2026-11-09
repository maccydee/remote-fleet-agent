"""Mac native evidence requires pinned SSH; local shell is insufficient."""
import pytest

pytestmark = pytest.mark.native


async def test_native_mac_pinned_ssh(native_manifest):
    module, manifest = native_manifest
    targets = [t for t in manifest["targets"] if t["platform"] == "macos"]
    if not targets:
        pytest.skip("BLOCKED: enrolled Mac with existing Remote Login required")
    for target in targets:
        result = await module.preflight(manifest, target)
        assert result["simulation"] is False
        assert result["status"] == "PASS"
