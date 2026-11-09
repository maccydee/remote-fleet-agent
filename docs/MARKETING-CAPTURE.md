# Marketing capture during validation

Record completed workflows while the browser test environment is ready. Public scenes use fictional clients, devices and issues, with Simulation visible throughout. Native Windows, Linux and Mac material stays private until separately reviewed.

The capture helper lives in `scripts/capture-showcase.py`. Its `CaptureSession` accepts an existing Playwright browser, creates a fresh 1440×900 context with video recording enabled before navigation, saves deliberate PNG checkpoints and closes the context before indexing WebM recordings. A 390×844 viewport can be supplied for narrow-screen checks. Browser credentials remain with the caller; the helper does not read tokens or sign in.

A passing test creates private, unreviewed source material. A failed test creates private debugging material. Neither enters the marketing-ready index automatically.

## Capture integration

Load the helper with `importlib.util.spec_from_file_location`, then construct `CaptureSession(browser, output, run_id, source_revision, visible_simulation_label=True)`. Use `session.context.new_page()`, run the real browser scenario and call `session.screenshot(page, "Operations attention queue")` at deliberate checkpoints. Finish with the actual scenario result, `session.finish("PASS")` or `session.finish("FAIL")`. This closes the recording context and returns a manifest path. The calling fixture must finish in teardown even when an assertion fails.

Select an account-private output directory outside the repository. `CaptureSession` creates a fresh random child, keeps source recordings separately and records actual PNG dimensions. Video dimensions come from the context's explicit recording size. Duration remains null unless measured; target clip lengths are editing guidance, not measurements.

Capture Operations overview/filtering, exact-device selection, investigation findings, repair review, pending approval, the human decision, execution/check results and failure/recovery. Capture provider conversation-to-review footage only in a clean lab-only client session. A browser recording cannot stand in for a terminal or AI-client recording.

## Review and index

Each manifest asset records its relative file path, scene, media type, dimensions, source revision/diff hash, UTC capture time, run id, scenario result, simulation/native environment, platform/provider, privacy class, review status, claim limitations and measured duration or null.

Open each selected screenshot and play each selected source recording. Check names, user content, notifications, credentials, URLs, file paths, visible Simulation status, readability, complete start/end and correspondence with the test outcome. Only then record `review_status: approved`, `reviewer`, `reviewed_at` and `privacy_class: public_candidate`. Native footage also requires `native_public_release_review: true`. These fields record a human review; no test or model should invent them.

```sh
.venv/bin/python scripts/capture-showcase.py /private/path/run/manifest.json \
  --output /private/path/reviewed-index
```

The generated contact index includes only existing, passing, reviewed candidates. All other assets appear in the private withheld list with a reason. Index generation never changes review state or publishes anything. File presence and metadata checks cannot establish that an image is safe; human visual inspection remains necessary.
