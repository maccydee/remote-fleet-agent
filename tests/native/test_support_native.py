"""Pinned identity preflight is separate from human-approved repair evidence."""
import pytest

pytestmark = pytest.mark.native


@pytest.mark.parametrize("platform", ["windows", "linux", "macos"])
async def test_enrolled_identity_preflight(native_manifest, platform):
    module, manifest = native_manifest
    targets = [t for t in manifest["targets"] if t["platform"] == platform]
    if not targets:
        pytest.skip("BLOCKED: no current enrolled " + platform + " target in manifest")
    for target in targets:
        result = await module.preflight(manifest, target)
        assert result["simulation"] is False
        assert result["expected_fingerprint"] == result["observed_fingerprint"]
        assert result["status"] == "PASS"


@pytest.mark.parametrize("platform", ["windows", "linux", "macos"])
async def test_scratch_cycle_requires_separate_human_decisions(native_lab_factory, platform):
    module, create = native_lab_factory
    lab = create(platform)
    try:
        result = await lab.run_approved_scratch_cycle()
    except module.PrerequisiteError as exc:
        if "Separate human decision pending" in str(exc):
            pytest.skip("BLOCKED: " + str(exc))
        raise
    assert result["status"] == "PASS"
    assert result["cleanup_verified"] is True
    assert result["user_outcome"] == "pending"
