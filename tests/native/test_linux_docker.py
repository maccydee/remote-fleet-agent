"""Native Docker evidence requires two separately pinned enrolled endpoints."""
import pytest

pytestmark = pytest.mark.native


async def test_linux_pair_has_distinct_pinned_identities(native_manifest):
    module, manifest = native_manifest
    targets = [t for t in manifest["targets"] if t["platform"] == "linux"]
    if len(targets) < 2:
        pytest.skip("BLOCKED: two enrolled Linux Docker targets required")
    assert len({(t["address"], t["port"]) for t in targets}) == len(targets)
    assert len({t["fingerprint"] for t in targets}) == len(targets)
    for target in targets:
        result = await module.preflight(manifest, target)
        assert result["status"] == "PASS"
