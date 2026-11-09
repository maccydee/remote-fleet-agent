"""Privacy/release decisions are independent from capture and scenario success."""
import importlib.util
from pathlib import Path

import pytest

SPEC = importlib.util.spec_from_file_location("capture_showcase", Path(__file__).parents[1] / "scripts" / "capture-showcase.py")
MODULE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(MODULE)


@pytest.fixture
def asset(tmp_path):
    (tmp_path / "scene.png").write_bytes(b"private test bytes")
    return {"asset": "scene.png", "scene": "Operations", "media_type": "image/png",
            "width": 1440, "height": 900, "source_revision": "test-diff-hash",
            "captured_at": "2026-11-08T12:00:00+00:00", "run_id": "browser-1",
            "scenario_result": "PASS", "environment": "simulation", "platform": None,
            "provider": None, "privacy_class": "public_candidate", "review_status": "approved",
            "reviewer": "Engineer", "reviewed_at": "2026-11-08T12:05:00+00:00",
            "visible_simulation_label": True, "limitations": ["Simulated data"], "duration_seconds": None}


@pytest.mark.parametrize("change", [
    {"asset": "missing.png"}, {"scenario_result": "FAIL"}, {"scenario_result": "NOT_RUN"},
    {"review_status": "pending"}, {"reviewer": ""}, {"privacy_class": "private"},
    {"environment": "native"}, {"visible_simulation_label": False}, {"width": None},
    {"source_revision": ""}, {"asset": "../scene.png"}, {"reviewed_at": "yesterday"},
    {"media_type": "text/html"}, {"duration_seconds": -1},
])
def test_incomplete_assets_withheld(tmp_path, asset, change):
    asset.update(change)
    result = MODULE.build_index([asset], tmp_path)
    assert result["public_candidates"] == []
    assert len(result["withheld"]) == 1


def test_reviewed_pass_is_candidate_without_changing_review(tmp_path, asset):
    before = dict(asset)
    assert MODULE.build_index([asset], tmp_path)["public_candidates"] == [asset]
    assert asset == before


def test_native_requires_additional_explicit_privacy_review(tmp_path, asset):
    asset.update(environment="native", native_public_release_review=True)
    assert MODULE.build_index([asset], tmp_path)["public_candidates"] == [asset]


def test_symlink_asset_is_withheld(tmp_path, asset):
    (tmp_path / "alias.png").symlink_to(tmp_path / "scene.png")
    asset["asset"] = "alias.png"
    assert not MODULE.build_index([asset], tmp_path)["public_candidates"]


def test_manifest_index_never_approves_pending_asset(tmp_path, asset):
    import json
    asset["review_status"] = "pending"
    manifest = tmp_path / "manifest.json"
    manifest.write_text(json.dumps({"schema": 1, "assets": [asset]}))
    MODULE.write_index(manifest, tmp_path / "index")
    assert json.loads(manifest.read_text())["assets"][0]["review_status"] == "pending"
    assert "scene.png" not in (tmp_path / "index" / "index.html").read_text()


def test_capture_context_starts_recording_before_scene_and_closes_before_index(tmp_path):
    import struct

    class Page:
        def screenshot(self, *, path, full_page):
            assert not full_page
            Path(path).write_bytes(b"\x89PNG\r\n\x1a\n" + b"\0" * 8 + struct.pack(">II", 1440, 900))

    class Browser:
        def new_context(self, **options):
            self.options = options
            return self

        def close(self):
            self.closed = True
            (Path(self.options["record_video_dir"]) / "actual.webm").write_bytes(b"test-only video fixture")

    browser = Browser()
    capture = MODULE.CaptureSession(browser, tmp_path, "test-run", "source-hash",
                                    visible_simulation_label=True)
    assert browser.options["record_video_size"] == {"width": 1440, "height": 900}
    capture.screenshot(Page(), "Operations")
    manifest = capture.finish("FAIL")
    import json
    assets = json.loads(manifest.read_text())["assets"]
    assert browser.closed
    assert len(assets) == 2
    assert all(a["scenario_result"] == "FAIL" and a["review_status"] == "pending" for a in assets)
    assert json.loads((manifest.parent / "review" / "review-index.json").read_text())["public_candidates"] == []
