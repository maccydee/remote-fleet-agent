"""Create a reviewed marketing index from private, test-linked capture manifests."""
from __future__ import annotations

import argparse
import html
import json
import mimetypes
import os
import struct
import uuid
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

REQUIRED = {"asset", "scene", "media_type", "width", "height", "source_revision",
            "captured_at", "run_id", "scenario_result", "environment", "platform",
            "provider", "privacy_class", "review_status", "limitations", "duration_seconds"}


def validate_asset(entry: dict[str, Any], root: Path) -> tuple[bool, str]:
    """Return publishability without modifying a human review decision."""
    if not REQUIRED <= entry.keys():
        return False, "missing provenance"
    root = root.resolve()
    asset = Path(entry["asset"])
    if asset.is_absolute() or ".." in asset.parts:
        return False, "asset must be relative to capture directory"
    path = root / asset
    if not path.is_file() or path.is_symlink() or not path.resolve().is_relative_to(root):
        return False, "missing or unsafe asset"
    if entry["scenario_result"] != "PASS":
        return False, "scenario did not pass"
    if entry["review_status"] != "approved" or not entry.get("reviewer") or not entry.get("reviewed_at"):
        return False, "human review required"
    if entry["privacy_class"] != "public_candidate":
        return False, "private asset"
    if entry["environment"] not in {"simulation", "native"}:
        return False, "invalid environment"
    if entry["environment"] == "native" and entry.get("native_public_release_review") is not True:
        return False, "separate native privacy review required"
    if entry["environment"] == "simulation" and entry.get("visible_simulation_label") is not True:
        return False, "visible simulation label required"
    if any(type(entry[k]) is not int or entry[k] <= 0 for k in ("width", "height")):
        return False, "actual dimensions required"
    expected = mimetypes.guess_type(path.name)[0]
    if entry["media_type"] != expected or expected not in {"image/png", "video/webm", "video/mp4"}:
        return False, "unsupported media type"
    if not isinstance(entry["limitations"], list) or not all(isinstance(s, str) for s in entry["limitations"]):
        return False, "limitations must be recorded"
    if not entry["run_id"] or not entry["source_revision"]:
        return False, "source and test run required"
    try:
        for key in ("captured_at", "reviewed_at"):
            if datetime.fromisoformat(entry[key]).tzinfo is None:
                return False, "timestamps require timezone"
    except (ValueError, TypeError):
        return False, "invalid timestamps"
    duration = entry["duration_seconds"]
    if duration is not None and (type(duration) not in (int, float) or duration <= 0):
        return False, "invalid measured duration"
    return True, "reviewed"


def build_index(entries: list[dict[str, Any]], root: Path) -> dict[str, Any]:
    """Separate approved public candidates from withheld/private source material."""
    accepted, withheld = [], []
    for entry in entries:
        valid, reason = validate_asset(entry, root)
        if valid:
            accepted.append(dict(entry))
        else:
            withheld.append({"asset": entry.get("asset"), "reason": reason})
    return {"schema": 1, "generated_at": datetime.now(UTC).isoformat(),
            "public_candidates": accepted, "withheld": withheld}


def write_index(manifest: Path, output: Path) -> dict[str, Any]:
    """Write a private index; no copying, uploading or automatic approvals."""
    raw = json.loads(manifest.read_text())
    entries = raw.get("assets")
    if raw.get("schema") != 1 or not isinstance(entries, list):
        raise ValueError("Expected schema 1 with an assets list")
    result = build_index(entries, manifest.parent)
    output.mkdir(mode=0o700, parents=True, exist_ok=True)
    (output / "review-index.json").write_text(json.dumps(result, indent=2))
    os.chmod(output / "review-index.json", 0o600)
    cards = []
    for entry in result["public_candidates"]:
        path = (manifest.parent / entry["asset"]).resolve().as_uri()
        title = html.escape(entry["scene"])
        label = html.escape(entry["environment"] + " · " + entry["run_id"])
        media = (f'<img src="{html.escape(path)}" alt="{title}" width="480">' if entry["media_type"] == "image/png" else f'<video src="{html.escape(path)}" controls width="480"></video>')
        cards.append(f'<article><h2>{title}</h2><p>{label}</p>{media}</article>')
    (output / "index.html").write_text('<!doctype html><meta charset="utf-8"><title>Reviewed capture candidates</title><h1>Reviewed capture candidates</h1>' + "".join(cards))
    os.chmod(output / "index.html", 0o600)
    return result


class CaptureSession:
    """Optional Playwright capture; accepts an existing Browser, never credentials.

    Call finish only after the browser scenario assertions have completed. Files
    remain private and unreviewed even when every assertion passed.
    """

    def __init__(self, browser: Any, output: Path, run_id: str, source_revision: str,
                 *, environment: str = "simulation", platform: str | None = None,
                 provider: str | None = None, visible_simulation_label: bool = False,
                 viewport: tuple[int, int] = (1440, 900)) -> None:
        if environment not in {"simulation", "native"}:
            raise ValueError("Capture environment must be simulation or native")
        if not run_id or not source_revision:
            raise ValueError("Capture requires test run and source revision/diff hash")
        self.directory = output.expanduser().resolve() / uuid.uuid4().hex
        self.directory.mkdir(mode=0o700, parents=True)
        self.recordings = self.directory / "source-recordings"
        self.recordings.mkdir(mode=0o700)
        self.context = browser.new_context(viewport={"width": viewport[0], "height": viewport[1]},
                                           record_video_dir=str(self.recordings),
                                           record_video_size={"width": viewport[0], "height": viewport[1]})
        self.entries: list[dict[str, Any]] = []
        self.base = {"source_revision": source_revision, "run_id": run_id,
                     "environment": environment, "platform": platform, "provider": provider,
                     "privacy_class": "private", "review_status": "pending",
                     "visible_simulation_label": visible_simulation_label,
                     "duration_seconds": None,
                     "limitations": ["Simulation; no native repair demonstrated"] if environment == "simulation" else ["Private native evidence; separate review required"]}
        self.closed = False
        self.viewport = viewport

    def _entry(self, path: Path, scene: str, media_type: str,
               dimensions: tuple[int, int]) -> dict[str, Any]:
        return {**self.base, "asset": str(path.relative_to(self.directory)), "scene": scene,
                "media_type": media_type, "width": dimensions[0], "height": dimensions[1],
                "captured_at": datetime.now(UTC).isoformat(), "scenario_result": "NOT_RUN"}

    def screenshot(self, page: Any, scene: str) -> Path:
        """Capture a deliberate checkpoint; PNG header supplies actual dimensions."""
        if self.closed:
            raise ValueError("Capture is already closed")
        path = self.directory / (uuid.uuid4().hex + ".png")
        page.screenshot(path=str(path), full_page=False)
        raw = path.read_bytes()
        if raw[:8] != b"\x89PNG\r\n\x1a\n":
            raise ValueError("Screenshot did not produce a PNG")
        dimensions = struct.unpack(">II", raw[16:24])
        self.entries.append(self._entry(path, scene, "image/png", dimensions))
        os.chmod(path, 0o600)
        return path

    def finish(self, scenario_result: str, scene: str = "Workflow recording") -> Path:
        """Close context to finalise real source recordings, then save provenance."""
        if self.closed:
            raise ValueError("Capture is already closed")
        if scenario_result not in {"PASS", "FAIL", "BLOCKED", "NOT_RUN"}:
            raise ValueError("Unknown scenario result")
        self.context.close()
        self.closed = True
        for path in sorted(self.recordings.glob("*.webm")):
            self.entries.append(self._entry(path, scene, "video/webm", self.viewport))
            os.chmod(path, 0o600)
        for entry in self.entries:
            entry["scenario_result"] = scenario_result
        manifest = self.directory / "manifest.json"
        manifest.write_text(json.dumps({"schema": 1, "assets": self.entries}, indent=2))
        os.chmod(manifest, 0o600)
        write_index(manifest, self.directory / "review")
        return manifest


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("manifest", type=Path)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    result = write_index(args.manifest, args.output)
    print(json.dumps({"public_candidates": len(result["public_candidates"]),
                      "withheld": len(result["withheld"])}))


if __name__ == "__main__":
    main()
