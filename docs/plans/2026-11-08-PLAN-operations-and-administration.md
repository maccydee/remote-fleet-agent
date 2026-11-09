# Operations workspace and remote administration implementation plan

> **For the executor:** implement this task-by-task with the executing-plans skill.

**Goal:** make Remote Fleet an oversight workspace for AI-led support, then deliver confirmed cross-platform investigation and engineer-approved bounded repairs with native validation.
**Architecture:** preserve the existing Python controller, SQLite store, verified SSH transport and provider-neutral stdio bridge. Ship an operations UI over v0.9 first, then add versioned investigation, plan and durable execution services behind capability flags. Engineer conversations stay in their chosen AI client.
**Tech Stack:** Python >=3.11, pytest/pytest-asyncio, SQLite, asyncssh, MCP SDK, plain JavaScript/HTML/CSS, optional pytest-playwright browser tests and Docker Compose lab.

## Progress

| Status | Count |
|--------|-------|
| 🔴 NOT_STARTED | 0 |
| 🟡 IN_PROGRESS | 0 |
| 🟢 COMPLETED | 18 |
| ⚪ BLOCKED | 5 |

Implementation and available automated verification are complete. Tasks 19–23 retain explicit release/evidence gates below; no feedback pause is requested. Final evidence is in the Codex task workspace `outputs/operations-release/verification.json`. Source changes remain uncommitted to preserve the mixed pre-existing baseline.

## Inputs and scope

Repository: `/Users/cal/remote-fleet-agent`. Read `/Users/cal/remote-fleet-agent/docs/plans/2026-11-08-DESIGN-ai-assisted-remote-administration.md` and `/Users/cal/remote-fleet-agent/docs/plans/2026-11-08-DESIGN-operations-workspace.md` first. Task order is intentional: tasks 1–8 deliver a useful UI on today's backend; 9–17 implement the agreed administration expansion; 18–22 validate native execution, host portability and release evidence. Task 23 captures marketing screenshots and real recordings during those validation milestones. No task is completed by writing this plan.

The user approved the UI direction and delegated remaining design choices. They requested coverage on Windows test PC DESKTOP-6PB6U6S, Linux in Docker and this native Mac. This document plans those tests; none has been run for these future changes. Keep current access expiry and authorisation policies. Stage any live change as a reviewed plan through the same workflow being tested.

## Working rules

Run commands from `/Users/cal/remote-fleet-agent` unless a task says otherwise. Use `.venv/bin/python`; on Windows use `.venv\Scripts\python.exe`. Read any newly encountered AGENTS.md before editing its subtree. Existing tree contains extensive uncommitted work: preserve it and save a baseline diff before starting. No resets, blanket git add or automatic publication. Stage only the reviewed task's changes (use git add -p for pre-existing modified files); commit after verification, or record a checkpoint if unrelated mixed changes cannot be safely separated. Existing untracked baseline files need deliberate review before staging.

Each task follows this cycle:
1. Add the shown regression test and the boundary cases in its contract. Imports/fixtures named below are planned additions; create them in the listed test module or shared fixture plugin.
2. Run `.venv/bin/python -m pytest <exact-test-file>::<shown-test-name> -v`. Expect an assertion failure for the missing behaviour. A typo/import/setup error is not the intended RED result; fix the test setup first. A new module's import error is only a temporary scaffold stage.
3. Implement the stated behaviour, using complete code below where provided. Mechanical rendering/wiring follows the concrete contracts. Keep scope to this task.
4. Run the task's specified checks. Expect PASS; investigate warnings. Refactor only with tests green.
5. Review the diff, stage the named changes and commit with `feat:`, `fix:` or `test:` plus the task title. Update this task's status and progress counts only after verification.

Test snippets show the assertion spine, not an invitation to assert fixture-generated answers. Exercise the real service/interpreter/API through the named fixture. Browser fixtures use the running Application and real DOM. Native fixtures use real enrolled SSH endpoints; simulated results never count as native proof.

For tests with three external boundaries, use this topology rather than a stack of unrelated mocks:

| Suite | Boundary | Replacement | Preserved behaviour |
|-------|----------|-------------|---------------------|
| Unit/service | SSH connection only | bounded fake streams/process | real store, roles, revisions, digest, scheduling |
| HTTP/browser/SDK | endpoint SSH only | forbidden connector or explicit simulation | real HTTP login/CSRF, stdio SDK, UI, database |
| Native | none on transport/execution | isolated scratch target only | pinned SSH, exact native scripts, human approval |

## Release gates and test matrix

| Gate | Required evidence | Scope limit |
|------|-------------------|-------------|
| UI milestone | scoped feed/API/browser/packaged asset tests | future admin controls hidden |
| Admin core | negative authorisation, mutation binding, crash/recovery and staging tests | no claim of read-only arbitrary code |
| Windows endpoint | current enrolled PC identity, scratch cycle, failure, recovery, cleanup | Outlook/driver repairs need separate evidence |
| Linux endpoints | two real Docker SSH endpoints, canary stop, unknown outcome | no native systemd/desktop/kernel claim |
| macOS endpoint | actual Mac over SSH, scratch cycle and context | no claim that GUI apps were exercised |
| Controller hosts | Mac/Windows/Linux Python/API/MCP/wheel results separately | target OS coverage does not prove host coverage |
| AI providers | SDK protocol plus actual installed-client smoke where available | no universal trusted in-client approval claim |

Every native report records run id, expected/observed public identity, OS/account, source revision or diff hash, commands/plan digest, decision actor/time, change/check/recovery outcomes, elapsed time (MEASURED), cleanup and limitations. Exclude tokens, private keys and unrelated user content. Store private results outside the repository. PASS, FAIL, BLOCKED and NOT_RUN are separate statuses. Native service/driver/GUI scenarios remain visible gaps until tested.

## Production readiness disposition

| Domain | Disposition |
|--------|-------------|
| Observability | Tasks 10, 13, 17 and 22: measured durations, state logs, correlation ids and result reports; per-run correlation supplies local tracing, no external tracing backend needed |
| Health & lifecycle | Task 17: authenticated health, scheduler readiness, bounded drain and restart handling |
| Resilience | Tasks 10, 12–14, 17: deadlines, stream limits, backpressure, single claims, unknown-state recovery; only read probes may retry with bounded backoff |
| Security | Tasks 2–3, 9–16, 18: server-side scope, trusted identity, human decisions, digest binding, no-secret tests and native guards |
| Configuration | Tasks 9–17: opt-in administration, queue/concurrency bounds, validated plan schema, loopback default, private credentials |
| Deployment | Tasks 8, 17, 20–22: wheel smoke, host matrix, self-hosted service guidance, backup/restore; lab CPU/memory limits retained |

### Task 1: Establish reproducible fixtures and a migration baseline 🟢 COMPLETED

**Files:**
- Create: `/Users/cal/remote-fleet-agent/tests/conftest_support.py`
- Create: `/Users/cal/remote-fleet-agent/tests/conftest.py`
- Create: `/Users/cal/remote-fleet-agent/tests/test_operations.py`
- Modify: `/Users/cal/remote-fleet-agent/tests/test_support.py`
- Modify: `/Users/cal/remote-fleet-agent/tests/test_live_workspace.py`

**Behavioral contract:**

| Input | Expected output |
|-------|-----------------|
| Existing simulated case | Remains readable with the same id, revision and repair digest |
| Unit or browser suite attempts endpoint SSH | Test fails immediately |
| Native test variables absent | Native suite is skipped with a prerequisite reason |

**Language Rules (Python):** use the project virtual environment and pytest; write the failing test first, verify the intended failure, implement, then verify PASS. Add public-function type hints. Run the configured Ruff checks. No type checker is configured today; record that rather than claiming a mypy pass. Prefer stdlib and lazy imports for optional dependencies. Raise specific actionable exceptions. Declare test dependencies in pyproject.toml and record installed versions in test evidence. Keep secrets outside committed files.

**Step 1 — RED:** add the following test, plus the boundary cases above. Include the relevant pytest/imports and the fixture described by this task.

```python
async def test_projection_fixture_never_connects(support_env):
    service, owner, tech, _ = support_env
    case = service.support.create(tech, "northstar-demo", {
        "host": "windows-01", "symptom": "Example failure", "profile": "general"})
    assert service.support.get(owner, "northstar-demo", case["id"])["revision"] == 1
```

**Step 2 — verify RED:** run `.venv/bin/python -m pytest /Users/cal/remote-fleet-agent/tests/test_operations.py::test_projection_fixture_never_connects -v`; expect failure for the missing contract after the fixture/import scaffolding exists.

**Step 3 — GREEN:**

Extract the setup pattern from tests/test_support.py into a shared support_env fixture: yield (service, owner, tech, directory), seed_demo into tmp_path, patch runtime.connect to fail, authenticate owner, and add a head-office-scoped technician. Close the service in fixture teardown. Register it through pytest_plugins = ["tests.conftest_support"] in tests/conftest.py. Create the file if absent. Keep the existing fixture tuple ordering intact in old tests or adapt every caller explicitly. Save a fixture case with missing new optional fields to test backwards-compatible reading. Capture baseline test counts and the existing Git status in the execution log; many existing project files are uncommitted.

**Step 4 — verify GREEN:** Run .venv/bin/python -m pytest tests/test_support.py tests/test_live_workspace.py tests/test_support_mcp.py -q. Existing behaviour passes; capture actual counts.

**Step 5 — checkpoint:** review only the listed file changes, stage deliberately, commit the verified task, update its status and the progress counts.

### Task 2: Provide a scoped all-client operations read model 🟢 COMPLETED

**Files:**
- Create: `/Users/cal/remote-fleet-agent/src/remote_fleet_agent/operations.py`
- Modify: `/Users/cal/remote-fleet-agent/src/remote_fleet_agent/live_service.py`
- Modify: `/Users/cal/remote-fleet-agent/src/remote_fleet_agent/live_store.py`
- Modify: `/Users/cal/remote-fleet-agent/tests/test_operations.py`
- Create: `/Users/cal/remote-fleet-agent/tests/test_operations_projection.py`

**Behavioral contract:**

| Input | Expected output |
|-------|-----------------|
| Technician scoped to Northstar head-office | Only those cases and counts; no Harbour or warehouse values |
| Same hostname in two clients | Distinct rows keyed by (client, case_id) and labelled with client |
| More than 100 cases | Older active cases remain discoverable; paging is explicit |
| Missing assignee or provider | Unassigned / Not recorded; no inferred identity |
| Unknown state | Needs review bucket; raw state retained |

**Language Rules (Python):** use the project virtual environment and pytest; write the failing test first, verify the intended failure, implement, then verify PASS. Add public-function type hints. Run the configured Ruff checks. No type checker is configured today; record that rather than claiming a mypy pass. Prefer stdlib and lazy imports for optional dependencies. Raise specific actionable exceptions. Declare test dependencies in pyproject.toml and record installed versions in test evidence. Keep secrets outside committed files.

**Step 1 — RED:** add the following test, plus the boundary cases above. Include the relevant pytest/imports and the fixture described by this task.

```python
async def test_operations_cannot_leak_other_clients(support_env):
    service, owner, tech, _ = support_env
    service.support.create(owner, "harbour-demo", {
        "host": "windows-01", "symptom": "PRIVATE-HARBOUR", "profile": "general"})
    result = service.operations(tech, {"limit": 25})
    assert "PRIVATE-HARBOUR" not in __import__("json").dumps(result)
    assert result["total"] == 0
```

**Step 2 — verify RED:** run `.venv/bin/python -m pytest /Users/cal/remote-fleet-agent/tests/test_operations.py::test_operations_cannot_leak_other_clients -v`; expect failure for the missing contract after the fixture/import scaffolding exists.

**Step 3 — GREEN:**

Add LiveService.operations(actor, filters) as an authorised read-only projection; it never opens SSH or starts jobs. Re-fetch the actor using store.operator. Enumerate only load_registry clients granted to that actor and use selected(actor, client, site) before counting or returning cases. Do not reuse SupportWorkflow.list's last-100 cap as a complete source. Add a scoped records iterator with bound SQL parameters and validated filters, yielding safe summaries without script bodies. Case identity is (client, id). Summary fields: id, client, client_name, site, host, platform, symptom (240 chars), state, revision, updated_at, engineer_id/name or null, simulation, bucket. Return items, total, counts_by_bucket, next_offset and partial_sources. Explicit requested unauthorised clients raise Denied; All silently omits them. A broken permitted source yields a labelled partial_sources entry without its raw path/error; other clients remain usable. A role/scope revocation invalidates the next read.

Use deterministic order (bucket rank, updated_at descending, client, id). Unknown/execution_failed/verification_failed/issue_persists -> attention; awaiting_approval -> approval; investigating/executing/approved -> active; resolved -> history; new/investigated/rejected -> open; unfamiliar states -> attention. Add scheduled work only from actual supported schedule records, labelled collection versus repair. Summary counts are over all authorised filtered records, before pagination. Bounded offset pagination is adequate for this first release; refresh may move rows, so deduplicate by composite identity and reset paging on refresh. Stable snapshot pagination is deferred and recorded honestly.

**Step 4 — verify GREEN:** Run .venv/bin/python -m pytest tests/test_operations.py -q. Include 151 cases, duplicate hostnames, disabled actor, site filters and one failing permitted source.

**Step 5 — checkpoint:** review only the listed file changes, stage deliberately, commit the verified task, update its status and the progress counts.

### Task 3: Expose the operations API without weakening session checks 🟢 COMPLETED

**Files:**
- Modify: `/Users/cal/remote-fleet-agent/src/remote_fleet_agent/live_app.py`
- Create: `/Users/cal/remote-fleet-agent/tests/test_operations_http.py`

**Behavioral contract:**

| Input | Expected output |
|-------|-----------------|
| GET /api/operations without session | Denied using existing response convention |
| GET with valid scoped session | Same projection as direct service call |
| limit 0, bool, 201 or offset -1 | 400 validation error |
| client filter denied | 403; no counts or device names |
| POST /api/operations | No mutation route |

**Language Rules (Python):** use the project virtual environment and pytest; write the failing test first, verify the intended failure, implement, then verify PASS. Add public-function type hints. Run the configured Ruff checks. No type checker is configured today; record that rather than claiming a mypy pass. Prefer stdlib and lazy imports for optional dependencies. Raise specific actionable exceptions. Declare test dependencies in pyproject.toml and record installed versions in test evidence. Keep secrets outside committed files.

**Step 1 — RED:** add the following test, plus the boundary cases above. Include the relevant pytest/imports and the fixture described by this task.

```python
async def test_operations_route_requires_login(tmp_path):
    from remote_fleet_agent.live_app import Application, seed_demo
    from remote_fleet_agent.live_store import Denied
    app = Application(seed_demo(tmp_path / "workspace"), 0)
    try:
        with pytest.raises(Denied):
            await app.dispatch("GET", "/api/operations", {})
    finally:
        app.close()
```

**Step 2 — verify RED:** run `.venv/bin/python -m pytest /Users/cal/remote-fleet-agent/tests/test_operations_http.py::test_operations_route_requires_login -v`; expect failure for the missing contract after the fixture/import scaffolding exists.

**Step 3 — GREEN:**

Insert GET /api/operations handling after session/actor validation and before the /api/clients route prefix check. Accept only client, site, platform, state, bucket, engineer, q, limit and offset. Reject unknown parameters, repeated singular values and invalid limits. Parse bounded integers explicitly (1..200; nonnegative offset), validate platform against windows/macos/linux, cap q to 240 characters and use equality for identifiers. Return service.operations. Keep Origin, Host, cookie, CSRF and disabled-operator checks unchanged. Extend real HTTP tests using Application and http.client, following tests/test_support_mcp.py; a direct dispatch test alone doesn't validate HTTP protections.

**Step 4 — verify GREEN:** Run .venv/bin/python -m pytest tests/test_operations_http.py tests/test_live_workspace.py -q. Exercise Host/Origin rejection, encoded filters, malformed query and role revocation.

**Step 5 — checkpoint:** review only the listed file changes, stage deliberately, commit the verified task, update its status and the progress counts.

### Task 4: Separate UI navigation and async request state 🟢 COMPLETED

**Execution 2026-11-08:** Implemented route ownership, generation guards, immediate loading state and history navigation; browser regressions pass.

**Files:**
- Create: `/Users/cal/remote-fleet-agent/src/remote_fleet_agent/app_assets/operations.js`
- Modify: `/Users/cal/remote-fleet-agent/src/remote_fleet_agent/app_assets/app.js`
- Modify: `/Users/cal/remote-fleet-agent/src/remote_fleet_agent/app_assets/index.html`
- Create: `/Users/cal/remote-fleet-agent/tests/browser/test_navigation.py`
- Modify: `/Users/cal/remote-fleet-agent/pyproject.toml`

**Behavioral contract:**

| Input | Expected output |
|-------|-----------------|
| Fresh signed-in visit | Operations and All permitted clients selected |
| Legacy client/support_case URL | Full case opens after sign-in |
| Browser Back/Forward | Restores page, filters and selected case |
| Slow response for old selection | Cannot overwrite current view |

**Language Rules (Python):** use the project virtual environment and pytest; write the failing test first, verify the intended failure, implement, then verify PASS. Add public-function type hints. Run the configured Ruff checks. No type checker is configured today; record that rather than claiming a mypy pass. Prefer stdlib and lazy imports for optional dependencies. Raise specific actionable exceptions. Declare test dependencies in pyproject.toml and record installed versions in test evidence. Keep secrets outside committed files.

**Step 1 — RED:** add the following test, plus the boundary cases above. Include the relevant pytest/imports and the fixture described by this task.

```python
def test_default_navigation(page, workspace_url, signed_in):
    page.goto(workspace_url)
    expect(page.get_by_role("heading", name="Operations", exact=True)).to_be_visible()
    expect(page.get_by_label("Client")).to_have_value("")
    expect(page.get_by_role("navigation").get_by_role("button", name="Devices", exact=True)).to_be_visible()
```

**Step 2 — verify RED:** run `.venv/bin/python -m pytest /Users/cal/remote-fleet-agent/tests/browser/test_navigation.py::test_default_navigation -v`; expect failure for the missing contract after the fixture/import scaffolding exists.

**Step 3 — GREEN:**

Retain plain JavaScript; add operations.js before app.js with defer and a small exported window.FleetOperations namespace. Add a request generation guard shared by operations, device search and case reads. Case mutation handlers capture {client,id,revision,digest} from the displayed review, never derive client from a later global filter. Update URL query state with page/client/site/support_case and handle popstate. Retain old query entry points and supported saved views through a route mapping. Keep global filter state separate from the case's immutable route identity. All-clients pages use /api/operations; client evidence pages continue existing scoped APIs. Add an optional browser-test dependency group using pytest-playwright; resolve and record exact installed versions at implementation time. Browser fixtures start Application on port 0, log in over HTTP, keep credentials in test memory, and forbid endpoint SSH. Use real DOM assertions rather than static asset substring tests.

**Step 4 — verify GREEN:** Run .venv/bin/python -m pytest tests/browser/test_navigation.py -q after installing the optional browser test group and its Chromium runtime. CI unit-only jobs use --ignore=tests/browser; the dedicated browser job installs that group. Browser tests use pytest.importorskip("playwright.sync_api") before importing expect so the default minimal dev environment can collect the non-browser suite. Explicitly import pytest and playwright.sync_api.expect in browser tests. Existing package-data wildcard includes new assets.

**Step 5 — checkpoint:** review only the listed file changes, stage deliberately, commit the verified task, update its status and the progress counts.

### Task 5: Build the operations landing page and scoped device search 🟢 COMPLETED

**Execution 2026-11-08:** Implemented scoped Operations, client/site/engineer/platform filters, device search and permanent device context.

**Files:**
- Modify: `/Users/cal/remote-fleet-agent/src/remote_fleet_agent/app_assets/operations.js`
- Modify: `/Users/cal/remote-fleet-agent/src/remote_fleet_agent/app_assets/app.js`
- Modify: `/Users/cal/remote-fleet-agent/src/remote_fleet_agent/app_assets/app.css`
- Create: `/Users/cal/remote-fleet-agent/tests/browser/test_operations.py`

**Behavioral contract:**

| Input | Expected output |
|-------|-----------------|
| Cases in several permitted clients | Client-labelled rows with filterable Issue/Client/site/Device/Engineer/State/Updated columns |
| Unknown result and approval waiting | Unknown outcome appears first; both link to exact case |
| Filter returns none | No matching work message; clear filters control |
| No clients/devices | Connect AI and enrollment guidance |
| Evidence collected yesterday | Last observed label; no Online claim |

**Language Rules (Python):** use the project virtual environment and pytest; write the failing test first, verify the intended failure, implement, then verify PASS. Add public-function type hints. Run the configured Ruff checks. No type checker is configured today; record that rather than claiming a mypy pass. Prefer stdlib and lazy imports for optional dependencies. Raise specific actionable exceptions. Declare test dependencies in pyproject.toml and record installed versions in test evidence. Keep secrets outside committed files.

**Step 1 — RED:** add the following test, plus the boundary cases above. Include the relevant pytest/imports and the fixture described by this task.

```python
def test_operation_opens_exact_client_case(page, workspace_url, signed_in, duplicate_cases):
    page.goto(workspace_url)
    row = page.get_by_role("row").filter(has_text="Harbour test failure")
    row.get_by_role("link", name="Open case").click()
    expect(page.get_by_test_id("case-client")).to_have_text("harbour-demo")
```

**Step 2 — verify RED:** run `.venv/bin/python -m pytest /Users/cal/remote-fleet-agent/tests/browser/test_operations.py::test_operation_opens_exact_client_case -v`; expect failure for the missing contract after the fixture/import scaffolding exists.

**Step 3 — GREEN:**

Build a compact table with a small operational count strip and search/filter row. Show attention, waiting decisions, active work and upcoming supported schedules. Global device search is read-only and scope-filtered on the server; add GET /api/devices with the same bounded filter parsing and projection pattern as operations, not all raw snapshots to the browser. A search result carries client/site/name/platform and last-observed timestamp. Hide row controls unless the existing capability is supported. Label simulated rows individually in mixed views. State partial data visibly and retain a retry-read action. Use esc for all record text. Use a refresh generation guard and preserve focused filter input; reset pagination when the filter changes. Do not imply current agent heartbeat presence.

**Step 4 — verify GREEN:** Run .venv/bin/python -m pytest tests/test_operations.py tests/test_operations_http.py tests/browser/test_operations.py -q. Add denied device search, keyboard search selection, duplicate hostname and hostile HTML-text tests.

**Step 5 — checkpoint:** review only the listed file changes, stage deliberately, commit the verified task, update its status and the progress counts.

### Task 6: Replace case dialogs with permanent review pages 🟢 COMPLETED

**Execution 2026-11-08:** Implemented permanent case and review routes; no untrusted text inserted as HTML.

**Files:**
- Modify: `/Users/cal/remote-fleet-agent/src/remote_fleet_agent/app_assets/app.js`
- Modify: `/Users/cal/remote-fleet-agent/src/remote_fleet_agent/app_assets/operations.js`
- Modify: `/Users/cal/remote-fleet-agent/src/remote_fleet_agent/app_assets/app.css`
- Create: `/Users/cal/remote-fleet-agent/tests/browser/test_case_review.py`

**Behavioral contract:**

| Input | Expected output |
|-------|-----------------|
| Case deep link | Full page with client, device, account, status and timestamps |
| Awaiting approval | Diagnosis/uncertainty, impact, exact scripts, checks and recovery visible before decision |
| Revision changes in another session | Old decision rejected; show refreshed review without auto-resubmission |
| checks_passed | Checks passed; issue confirmation remains pending |
| unknown | Outcome unknown; no unqualified retry repair |

**Language Rules (Python):** use the project virtual environment and pytest; write the failing test first, verify the intended failure, implement, then verify PASS. Add public-function type hints. Run the configured Ruff checks. No type checker is configured today; record that rather than claiming a mypy pass. Prefer stdlib and lazy imports for optional dependencies. Raise specific actionable exceptions. Declare test dependencies in pyproject.toml and record installed versions in test evidence. Keep secrets outside committed files.

**Step 1 — RED:** add the following test, plus the boundary cases above. Include the relevant pytest/imports and the fixture described by this task.

```python
def test_unknown_case_has_no_blind_retry(page, case_url, unknown_case, signed_in):
    page.goto(case_url)
    expect(page.get_by_text("Outcome unknown", exact=True)).to_be_visible()
    expect(page.get_by_role("button", name="Retry repair", exact=True)).to_have_count(0)
    expect(page.get_by_role("heading", name="Activity", exact=True)).to_be_visible()
```

**Step 2 — verify RED:** run `.venv/bin/python -m pytest /Users/cal/remote-fleet-agent/tests/browser/test_case_review.py::test_unknown_case_has_no_blind_retry -v`; expect failure for the missing contract after the fixture/import scaffolding exists.

**Step 3 — GREEN:**

Split supportDetail into data loading and a full-page renderer. Reuse existing support-cases API and exact script review; keep dialogues only for small confirmation/forms. Render a summary header, review panel and ordered activity timeline with expandable evidence/stdout/stderr. Derive early timeline entries from existing case fields without fabricating timestamps or engineers; fetch archived attempts through the existing history endpoint. Current case uses present(case), and historical records stay paged/bounded. Stale approve/reject/execute response clears the stale action state, reloads and requires a new human click. Preserve buttons available in v0.9 without advertising future cancellation/scheduling controls. Output truncation and simulation remain explicit. Closing a dialog no longer dismisses the case route.

**Step 4 — verify GREEN:** Run .venv/bin/python -m pytest tests/browser/test_case_review.py tests/test_support.py -q. Include long scripts, expanded history, revoked manager, special characters, two tabs and refresh during review.

**Step 5 — checkpoint:** review only the listed file changes, stage deliberately, commit the verified task, update its status and the progress counts.

### Task 7: Complete approvals, history, settings and legacy navigation 🟢 COMPLETED

**Execution 2026-11-08:** Implemented Approvals, History, Settings and accessible legacy tools; legacy routes remain covered.

**Files:**
- Modify: `/Users/cal/remote-fleet-agent/src/remote_fleet_agent/app_assets/app.js`
- Modify: `/Users/cal/remote-fleet-agent/src/remote_fleet_agent/app_assets/operations.js`
- Modify: `/Users/cal/remote-fleet-agent/src/remote_fleet_agent/live_service.py`
- Create: `/Users/cal/remote-fleet-agent/tests/browser/test_workspace_sections.py`
- Modify: `/Users/cal/remote-fleet-agent/tests/test_operations.py`

**Behavioral contract:**

| Input | Expected output |
|-------|-----------------|
| Primary navigation | Operations, Devices, Approvals, History, Settings |
| Observer approval page | Permitted status visible; decision unavailable server-side |
| Old Evidence/Compare/Reports/Onboarding link | Reaches equivalent client/device/settings page |
| Pending target confirmation before backend feature exists | No placeholder executable control |
| AI connection page | Copyable host-local setup instructions; no embedded chat or secret values |

**Language Rules (Python):** use the project virtual environment and pytest; write the failing test first, verify the intended failure, implement, then verify PASS. Add public-function type hints. Run the configured Ruff checks. No type checker is configured today; record that rather than claiming a mypy pass. Prefer stdlib and lazy imports for optional dependencies. Raise specific actionable exceptions. Declare test dependencies in pyproject.toml and record installed versions in test evidence. Keep secrets outside committed files.

**Step 1 — RED:** add the following test, plus the boundary cases above. Include the relevant pytest/imports and the fixture described by this task.

```python
def test_primary_navigation_has_five_sections(page, workspace_url, signed_in):
    page.goto(workspace_url)
    nav = page.get_by_role("navigation")
    for name in ("Operations", "Devices", "Approvals", "History", "Settings"):
        expect(nav.get_by_role("button", name=name, exact=True)).to_be_visible()
    expect(nav.get_by_role("button", name="Evidence", exact=True)).to_have_count(0)
```

**Step 2 — verify RED:** run `.venv/bin/python -m pytest /Users/cal/remote-fleet-agent/tests/browser/test_workspace_sections.py::test_primary_navigation_has_five_sections -v`; expect failure for the missing contract after the fixture/import scaffolding exists.

**Step 3 — GREEN:**

Use the same scoped projection with bucket=approval for Approvals and bucket=history for resolved work. Keep failed/unknown work in attention until reconciled, also searchable from History with an explicit state filter. Settings has Access, Clients/sites, AI connections and Schedules subsections; Reports/Compare are client tools; inventory/evidence are device tabs. Preserve manual evidence, follow-ups, collection history, branding, operator management and report export; add a migration map documenting each former destination. Old collection schedules remain labelled collections. Browser approval remains the working universal fallback. Keep generated provider instructions host-specific, and use existing generate_configs rather than editing user AI settings.

**Step 4 — verify GREEN:** Run .venv/bin/python -m pytest tests/browser/test_workspace_sections.py tests/test_live_workspace.py tests/test_integration_compatibility.py -q. Exercise every old navigation destination at least once.

**Step 5 — checkpoint:** review only the listed file changes, stage deliberately, commit the verified task, update its status and the progress counts.

### Task 8: Verify accessibility, packaging and the first UI milestone 🟢 COMPLETED

**Execution 2026-11-08:** 26 real Chromium browser tests pass. Final wheel includes UI assets; mobile, stale-response and navigation coverage pass.

**Files:**
- Create: `/Users/cal/remote-fleet-agent/tests/browser/test_workspace_accessibility.py`
- Modify: `/Users/cal/remote-fleet-agent/.github/workflows/compatibility.yml`
- Modify: `/Users/cal/remote-fleet-agent/docs/LIVE-WORKSPACE.md`
- Modify: `/Users/cal/remote-fleet-agent/docs/AI-SUPPORT.md`

**Behavioral contract:**

| Input | Expected output |
|-------|-----------------|
| 1440px and 390px viewport | No lost approval controls; tables scroll within their container |
| Keyboard-only engineer | Reach filters, case, inspect scripts and reject approval |
| Fresh installed wheel | New JS and CSS served successfully |
| Simulated screenshot | Simulation label visible; no credentials or real client details |

**Language Rules (Python):** use the project virtual environment and pytest; write the failing test first, verify the intended failure, implement, then verify PASS. Add public-function type hints. Run the configured Ruff checks. No type checker is configured today; record that rather than claiming a mypy pass. Prefer stdlib and lazy imports for optional dependencies. Raise specific actionable exceptions. Declare test dependencies in pyproject.toml and record installed versions in test evidence. Keep secrets outside committed files.

**Step 1 — RED:** add the following test, plus the boundary cases above. Include the relevant pytest/imports and the fixture described by this task.

```python
def test_case_review_is_keyboard_accessible(page, case_url, signed_in):
    page.set_viewport_size({"width": 390, "height": 844})
    page.goto(case_url)
    page.get_by_role("button", name="Reject / revoke approval").focus()
    expect(page.get_by_role("button", name="Reject / revoke approval")).to_be_focused()
    assert page.evaluate("document.documentElement.scrollWidth <= window.innerWidth")
```

**Step 2 — verify RED:** run `.venv/bin/python -m pytest /Users/cal/remote-fleet-agent/tests/browser/test_workspace_accessibility.py::test_case_review_is_keyboard_accessible -v`; expect failure for the missing contract after the fixture/import scaffolding exists.

**Step 3 — GREEN:**

Use actual text contrast, visible focus, labelled fields and status text with colour as a secondary cue. Add browser smoke in one CI job; keep Python/service tests on existing OS matrix. Test a built wheel in a clean virtualenv with importlib.resources, including operations.js and logo. Save fictional screenshots for Operations, approval, case timeline and device detail; replace old marketing shots only when the working UI is validated. Document this UI milestone independently from the subsequent administration work. Browser failures must report console errors and screenshot paths with private test data only.

**Step 4 — verify GREEN:** Run .venv/bin/python -m pytest tests/browser -q, .venv/bin/python -m pytest -q, and .venv/bin/python -m ruff check src tests. Capture actual counts and any pre-existing lint failures. Build wheel using pip wheel --no-deps and serve the installed artifact.

**Step 5 — checkpoint:** review only the listed file changes, stage deliberately, commit the verified task, update its status and the progress counts.

### Task 9: Add target-confirmed investigation sessions 🟢 COMPLETED

**Execution 2026-11-08:** Implemented per-device investigation confirmation with current actor/scope/identity checks and revision binding.

**Files:**
- Create: `/Users/cal/remote-fleet-agent/src/remote_fleet_agent/investigation.py`
- Modify: `/Users/cal/remote-fleet-agent/src/remote_fleet_agent/support.py`
- Modify: `/Users/cal/remote-fleet-agent/src/remote_fleet_agent/live_app.py`
- Modify: `/Users/cal/remote-fleet-agent/src/remote_fleet_agent/support_mcp.py`
- Create: `/Users/cal/remote-fleet-agent/tests/test_investigation.py`

**Behavioral contract:**

| Input | Expected output |
|-------|-----------------|
| Session requested for named device | pending_target_confirmation; SSH not opened |
| Engineer confirms same target digest | active session; repeated diagnostics need no new confirmation |
| Host/port/account/platform/key identity changes | Session invalid; new confirmation required |
| New group member | Not included in approved explicit target set |
| Technician invokes decision directly | Denied |

**Language Rules (Python):** use the project virtual environment and pytest; write the failing test first, verify the intended failure, implement, then verify PASS. Add public-function type hints. Run the configured Ruff checks. No type checker is configured today; record that rather than claiming a mypy pass. Prefer stdlib and lazy imports for optional dependencies. Raise specific actionable exceptions. Declare test dependencies in pyproject.toml and record installed versions in test evidence. Keep secrets outside committed files.

**Step 1 — RED:** add the following test, plus the boundary cases above. Include the relevant pytest/imports and the fixture described by this task.

```python
async def test_target_change_requires_new_confirmation(investigation_env):
    session = investigation_env.confirmed_session("windows-01")
    investigation_env.change_endpoint("windows-01", port=2223)
    with pytest.raises(Conflict, match="target"):
        investigation_env.validate_session(session["id"])
    assert investigation_env.ssh_calls == []
```

**Step 2 — verify RED:** run `.venv/bin/python -m pytest /Users/cal/remote-fleet-agent/tests/test_investigation.py::test_target_change_requires_new_confirmation -v`; expect failure for the missing contract after the fixture/import scaffolding exists.

**Step 3 — GREEN:**

Store investigation sessions separately from cases, with client, explicit targets, initiating actor, confirming engineer, target_digest, timestamps and closed state. Bind canonical ordered target snapshots to name, SSH account/address/port, OS and enrolled host-key identity. Hash public identity/config references, never serialize private keys. A changed permissions record or expiry is checked live even if target digest stays the same. Use revision CAS for confirm and close. Require the existing approve_repairs permission for a human confirmation initially; technician credentials can request and inspect but cannot confirm. Add request_investigation and run_investigation_command MCP tools, not an AI-callable confirm tool. Surface pending target confirmations in Approvals with purpose=investigation, distinct from repairs. Keep fixed evidence-only inventory capabilities available under their existing narrower contract.

**Step 4 — verify GREEN:** Run .venv/bin/python -m pytest tests/test_investigation.py tests/test_support_mcp.py -q. Include case closure, actor disabled, expiry and cross-client replay.

**Step 5 — checkpoint:** review only the listed file changes, stage deliberately, commit the verified task, update its status and the progress counts.

### Task 10: Execute bounded custom diagnostics across the three platforms 🟢 COMPLETED

**Execution 2026-11-08:** Implemented bounded Windows/macOS/Linux diagnostic dispatch, stream/deadline limits, per-endpoint exclusion and full paginated command history. Custom scripts retain account privileges.

**Files:**
- Modify: `/Users/cal/remote-fleet-agent/src/remote_fleet_agent/investigation.py`
- Modify: `/Users/cal/remote-fleet-agent/src/remote_fleet_agent/support.py`
- Modify: `/Users/cal/remote-fleet-agent/src/remote_fleet_agent/config.py`
- Create: `/Users/cal/remote-fleet-agent/tests/test_custom_diagnostics.py`

**Behavioral contract:**

| Input | Expected output |
|-------|-----------------|
| Active confirmed Windows session | Encoded PowerShell via run_bounded |
| Active confirmed macOS/Linux session | POSIX shell via run_bounded |
| Audit-only mode or administration disabled | Custom privileged scripts denied; fixed evidence checks retained |
| Timeout or SSH loss | Outcome uncertain, bounded output retained; no replay |
| Diagnostic output contains instructions | Returned as untrusted evidence, never parsed as approval |

**Language Rules (Python):** use the project virtual environment and pytest; write the failing test first, verify the intended failure, implement, then verify PASS. Add public-function type hints. Run the configured Ruff checks. No type checker is configured today; record that rather than claiming a mypy pass. Prefer stdlib and lazy imports for optional dependencies. Raise specific actionable exceptions. Declare test dependencies in pyproject.toml and record installed versions in test evidence. Keep secrets outside committed files.

**Step 1 — RED:** add the following test, plus the boundary cases above. Include the relevant pytest/imports and the fixture described by this task.

```python
@pytest.mark.parametrize("platform", ["windows", "macos", "linux"])
async def test_custom_diagnostic_requires_confirmed_context(diag_env, platform):
    with pytest.raises(Denied):
        await diag_env.run(platform, session_id="missing", script="echo test")
    assert diag_env.ssh_calls == []
```

**Step 2 — verify RED:** run `.venv/bin/python -m pytest /Users/cal/remote-fleet-agent/tests/test_custom_diagnostics.py::test_custom_diagnostic_requires_confirmed_context -v`; expect failure for the missing contract after the fixture/import scaffolding exists.

**Step 3 — GREEN:**

Validate nonempty NUL-free text, 32768-character script limit and integer timeout 5..300 (reject bool). Recheck actor, client/site, session identity, administration opt-in and audit-only policy immediately before connection and dispatch. Use run_bounded and the existing per-device exclusion mechanism, then persist command digest, execution identity, bounded streams, real timestamps and outcome. Enforce one overall monotonic deadline across connect/start/read/drain rather than a fresh full timeout for each phase. Record actual elapsed_seconds (MEASURED with monotonic), not zero placeholders. Keep simulation unable to interpret scripts. Document that arbitrary privileged diagnostic scripts can mutate state; permissions and identity are enforceable, a read-only label isn't. Never auto-elevate or supply sudo passwords.

**Step 4 — verify GREEN:** Run .venv/bin/python -m pytest tests/test_custom_diagnostics.py tests/test_support.py -q. Test output pressure on both streams, cancellation before and after dispatch, invalid timeout, macOS command selection and simulation malicious-marker script.

**Step 5 — checkpoint:** review only the listed file changes, stage deliberately, commit the verified task, update its status and the progress counts.

### Task 11: Define a versioned bounded executable plan 🟢 COMPLETED

**Execution 2026-11-08:** Implemented immutable schema-v1 executable plans, server-captured identities, strict predicates and bounded scripts/targets.

**Files:**
- Create: `/Users/cal/remote-fleet-agent/src/remote_fleet_agent/repair_plan.py`
- Create: `/Users/cal/remote-fleet-agent/tests/test_repair_plan.py`
- Modify: `/Users/cal/remote-fleet-agent/src/remote_fleet_agent/support.py`

**Behavioral contract:**

| Input | Expected output |
|-------|-----------------|
| Valid schema 1 with one target/step | Canonical digest and inspectable payload |
| Target/branch/package/hash/recovery/window edited | Digest changes; prior approval invalid |
| Duplicate step id, forward/cyclic reference or unknown key | ValueError with field path |
| Missing platform-specific step | Plan rejected before approval |
| NaN, bool timeout, naive datetime | Rejected |

**Language Rules (Python):** use the project virtual environment and pytest; write the failing test first, verify the intended failure, implement, then verify PASS. Add public-function type hints. Run the configured Ruff checks. No type checker is configured today; record that rather than claiming a mypy pass. Prefer stdlib and lazy imports for optional dependencies. Raise specific actionable exceptions. Declare test dependencies in pyproject.toml and record installed versions in test evidence. Keep secrets outside committed files.

**Step 1 — RED:** add the following test, plus the boundary cases above. Include the relevant pytest/imports and the fixture described by this task.

```python
def test_every_executable_field_changes_approval_digest(valid_plan):
    import copy
    from remote_fleet_agent.repair_plan import canonical_plan, plan_digest
    first = plan_digest(canonical_plan(valid_plan))
    changed = copy.deepcopy(valid_plan)
    changed["steps"][0]["script"] += "\necho changed"
    assert plan_digest(canonical_plan(changed)) != first
```

**Step 2 — verify RED:** run `.venv/bin/python -m pytest /Users/cal/remote-fleet-agent/tests/test_repair_plan.py::test_every_executable_field_changes_approval_digest -v`; expect failure for the missing contract after the fixture/import scaffolding exists.

**Step 3 — GREEN:**

Use a JSON schema enforced by stdlib validation with exact allowed keys and explicit errors. A plan has schema=1, client, explicit target snapshots, rationale/uncertainty/impact, time window in UTC plus original IANA timezone, disruption policy, trial/batches, stop policy, and ordered steps. Each step has id, target names, platform, script, timeout_seconds, kind (check/change/recovery), optional when predicate, and named package references with immutable version/hash. Parameters are fully resolved before approval; avoid free-form templating during execution. Predicates may inspect an earlier check's passed value or exit_code using equals, with strict type equality; unknown results evaluate to indeterminate and pause, never false-success. Only prior step ids can be referenced, removing cycles. Verification and recovery scripts are executable reviewed steps too. Limit 100 targets, 100 steps, 32768 characters per script and 1 MiB canonical payload. Reject unsupported keys. Preserve v0.9 payload reading as schema=legacy; retain its exact digest, do not silently reinterpret old approvals as schema 1.

Digest primitive (full code; schema/type validation precedes it):
```python
import hashlib
import json

def plan_digest(payload: dict) -> str:
    encoded = json.dumps(payload, sort_keys=True, separators=(",", ":"),
                         ensure_ascii=False, allow_nan=False).encode("utf-8")
    if len(encoded) > 1_048_576:
        raise ValueError("plan exceeds 1 MiB")
    return hashlib.sha256(encoded).hexdigest()
```
This is the canonical approval representation; both UI and MCP receive its digest and complete inspectable payload. A digest is integrity binding, not human authentication.

**Step 4 — verify GREEN:** Run .venv/bin/python -m pytest tests/test_repair_plan.py -q. Parametrize mutations across every executable field and both window bounds; test unicode, empty lists, duplicate targets and 101-step rejection.

**Step 5 — checkpoint:** review only the listed file changes, stage deliberately, commit the verified task, update its status and the progress counts.

### Task 12: Persist per-step claims and shared device exclusion 🟢 COMPLETED

**Execution 2026-11-08:** Implemented durable per-step intent, cross-controller endpoint claims, alias exclusion and unknown-state retention; legacy execution shares the guard.

**Files:**
- Create: `/Users/cal/remote-fleet-agent/src/remote_fleet_agent/repair_execution.py`
- Modify: `/Users/cal/remote-fleet-agent/src/remote_fleet_agent/live_store.py`
- Modify: `/Users/cal/remote-fleet-agent/src/remote_fleet_agent/maintenance.py`
- Modify: `/Users/cal/remote-fleet-agent/src/remote_fleet_agent/support.py`
- Create: `/Users/cal/remote-fleet-agent/tests/test_repair_claims.py`

**Behavioral contract:**

| Input | Expected output |
|-------|-----------------|
| Two controllers claim same approved step | Exactly one claim succeeds |
| Two cases target same client/device | Second pauses before dispatch |
| Controller dies after dispatch intent recorded | Recovery state unknown; no automatic replay |
| Same hostname across clients, different endpoints | Distinct lock identities |
| Same verified endpoint registered under two aliases | One shared exclusion claim |
| Legacy maintenance overlaps new repair | Same exclusion policy applies |

**Language Rules (Python):** use the project virtual environment and pytest; write the failing test first, verify the intended failure, implement, then verify PASS. Add public-function type hints. Run the configured Ruff checks. No type checker is configured today; record that rather than claiming a mypy pass. Prefer stdlib and lazy imports for optional dependencies. Raise specific actionable exceptions. Declare test dependencies in pyproject.toml and record installed versions in test evidence. Keep secrets outside committed files.

**Step 1 — RED:** add the following test, plus the boundary cases above. Include the relevant pytest/imports and the fixture described by this task.

```python
async def test_step_claim_is_single_use(execution_env):
    a, b = execution_env.controllers()
    first = a.claim_step(execution_env.run_id, "change-1")
    assert first is True
    assert b.claim_step(execution_env.run_id, "change-1") is False
    assert execution_env.dispatch_count == 0
```

**Step 2 — verify RED:** run `.venv/bin/python -m pytest /Users/cal/remote-fleet-agent/tests/test_repair_claims.py::test_step_claim_is_single_use -v`; expect failure for the missing contract after the fixture/import scaffolding exists.

**Step 3 — GREEN:**

Create additive SQLite execution_runs, execution_steps and endpoint_claims tables; migration is versioned and repeatable, with a database backup first. Step identity is (run_id, target, step_id); store phase, dispatch_intent_at, result, revision and plan_digest. Device exclusion key is the verified endpoint identity (public host-key fingerprint, canonical address, port), shared across aliases and clients; client, enrollment name and account remain claim metadata and independently authorised scope. Multiple accounts on the same verified endpoint share the exclusion claim. Canonicalise equivalent enrolled addresses where possible; copied host keys and unresolved network aliases require explicit enrollment reconciliation. A tenant-scoped row id alone cannot prevent two aliases changing the same device. Use BEGIN IMMEDIATE plus conditional UPDATE/INSERT within one transaction before any SSH call. Example transaction shape:
```sql
BEGIN IMMEDIATE;
UPDATE execution_steps SET phase='dispatch_intent', revision=revision+1
 WHERE run_id=? AND target=? AND step_id=? AND phase='pending';
-- Require rowcount=1. In the same transaction insert endpoint_claims;
-- UNIQUE endpoint key prevents overlapping owners. Roll back on conflict.
COMMIT;
```
Persist dispatch_intent before send (WHY: a crash between send and result storage cannot be distinguished from a completed remote command). On startup convert stranded dispatch_intent/running steps to unknown and pause their run; leave the endpoint claim pending reconciliation. Release ownership only when a known completion or explicit engineer reconciliation establishes it. Do not expire claims into an automatic retry. Reuse one store transaction helper with rollback on all exceptions, and re-fetch revisions. Integrate legacy maintenance with this shared guard rather than acquiring inconsistent lock orders. File locks can remain defence in depth on one machine, not the only durable authority.

**Step 4 — verify GREEN:** Run .venv/bin/python -m pytest tests/test_repair_claims.py tests/test_support.py tests/test_maintenance.py -q. Use two actual store connections, forced rollback, duplicate claim and crash-state reopen tests.

**Step 5 — checkpoint:** review only the listed file changes, stage deliberately, commit the verified task, update its status and the progress counts.

### Task 13: Execute bounded steps, checks and pre-approved recovery 🟢 COMPLETED

**Execution 2026-11-08:** Implemented check/change/recovery execution with explicit preconditions and exact platform scripts. Malformed checks remain indeterminate; post-change checks are separate from user resolution.

**Files:**
- Modify: `/Users/cal/remote-fleet-agent/src/remote_fleet_agent/repair_execution.py`
- Modify: `/Users/cal/remote-fleet-agent/src/remote_fleet_agent/support.py`
- Create: `/Users/cal/remote-fleet-agent/tests/test_repair_execution.py`

**Behavioral contract:**

| Input | Expected output |
|-------|-----------------|
| Approved change then JSON passed=true check | checks_passed; user outcome remains pending |
| Malformed/truncated/exit-nonzero check | verification_failed or indeterminate; no resolved claim |
| Failure with applicable approved recovery | Run exact recovery steps once and record separately |
| Unknown execution outcome | Pause; no recovery without established preconditions |
| New unapproved step | Denied |

**Language Rules (Python):** use the project virtual environment and pytest; write the failing test first, verify the intended failure, implement, then verify PASS. Add public-function type hints. Run the configured Ruff checks. No type checker is configured today; record that rather than claiming a mypy pass. Prefer stdlib and lazy imports for optional dependencies. Raise specific actionable exceptions. Declare test dependencies in pyproject.toml and record installed versions in test evidence. Keep secrets outside committed files.

**Step 1 — RED:** add the following test, plus the boundary cases above. Include the relevant pytest/imports and the fixture described by this task.

```python
async def test_unknown_outcome_never_replays_or_guesses_recovery(execution_env):
    execution_env.transport.disconnect_after_send = True
    await execution_env.run()
    assert execution_env.run_state == "unknown"
    assert execution_env.sent_step_ids == ["change-1"]
    await execution_env.restart_controller()
    assert execution_env.sent_step_ids == ["change-1"]
```

**Step 2 — verify RED:** run `.venv/bin/python -m pytest /Users/cal/remote-fleet-agent/tests/test_repair_execution.py::test_unknown_outcome_never_replays_or_guesses_recovery -v`; expect failure for the missing contract after the fixture/import scaffolding exists.

**Step 3 — GREEN:**

Implement the interpreter over immutable validated steps: recheck approval, actor/approver scope and endpoint identity, evaluate declared predicate, claim, dispatch, save known result or unknown, then choose the next declared step. No dynamic code generation or eval. Passed means exit_code==0, output untruncated, valid JSON object and passed is exactly True; 1 and "true" fail. A known change failure may trigger only reviewed recovery whose predicate is established from stored results. A recovery failure pauses for engineer action. Exception messages include case/run/step context without scripts or credentials. Emit state-transition logs and measured elapsed_seconds. Retain the existing separate user resolution record.

**Step 4 — verify GREEN:** Run .venv/bin/python -m pytest tests/test_repair_execution.py -q. Cover recovery once, mismatched digest before send, connection loss during recovery, strict booleans and inherited verification timeout.

**Step 5 — checkpoint:** review only the listed file changes, stage deliberately, commit the verified task, update its status and the progress counts.

### Task 14: Enforce windows, disruption, cancellation and staged rollouts 🟢 COMPLETED

**Execution 2026-11-08:** Implemented windows, readiness, cancellation, trial stop, failure threshold and controller fencing; no replay of uncertain dispatch.

**Files:**
- Create: `/Users/cal/remote-fleet-agent/src/remote_fleet_agent/repair_scheduler.py`
- Modify: `/Users/cal/remote-fleet-agent/src/remote_fleet_agent/repair_execution.py`
- Modify: `/Users/cal/remote-fleet-agent/src/remote_fleet_agent/live_service.py`
- Create: `/Users/cal/remote-fleet-agent/tests/test_repair_scheduler.py`

**Behavioral contract:**

| Input | Expected output |
|-------|-----------------|
| Approved readiness-required plan | Waits until a separate authenticated ready decision |
| Outside UTC execution window | Waits before opening connection |
| Window expires mid-step | Let bounded in-flight step report; stop further steps; approved recovery only within its declared window |
| Canary fails or is unknown | Dispatch count for other devices stays zero |
| Cancel before dispatch | No SSH; cancelled |
| Cancel after dispatch | Cancellation requested; reconcile current outcome; remaining steps stopped |

**Language Rules (Python):** use the project virtual environment and pytest; write the failing test first, verify the intended failure, implement, then verify PASS. Add public-function type hints. Run the configured Ruff checks. No type checker is configured today; record that rather than claiming a mypy pass. Prefer stdlib and lazy imports for optional dependencies. Raise specific actionable exceptions. Declare test dependencies in pyproject.toml and record installed versions in test evidence. Keep secrets outside committed files.

**Step 1 — RED:** add the following test, plus the boundary cases above. Include the relevant pytest/imports and the fixture described by this task.

```python
async def test_failed_canary_stops_batch(scheduler_env):
    scheduler_env.canary_result = "verification_failed"
    await scheduler_env.tick()
    assert scheduler_env.targets_dispatched == ["linux-a"]
    assert scheduler_env.run_state == "paused"
```

**Step 2 — verify RED:** run `.venv/bin/python -m pytest /Users/cal/remote-fleet-agent/tests/test_repair_scheduler.py::test_failed_canary_stops_batch -v`; expect failure for the missing contract after the fixture/import scaffolding exists.

**Step 3 — GREEN:**

Use UTC-aware now supplied by an injectable clock and original IANA zone for display. Reject ambiguous/nonexistent local maintenance times unless converted to an explicit offset by the engineer. Recheck permissions, plan digest, host identity and relevant declared predicates before each dispatch. Batch membership is an approved explicit list partitioning the target set; trial first, then bounded batches. Default stop threshold is one failed or unknown target; other thresholds must be reviewed. Optional post-trial human readiness is persisted. Each step's allowed window is explicit; define recovery window separately when recovery after normal window is intended. Cancellation stops future dispatches and signals current tasks best-effort; report that closing SSH doesn't prove remote code stopped. Expected reboot/network-loss steps become awaiting_reconciliation after disconnect; reconnect is a new identity-verified observation, never proof that the original operation succeeded. Do not ship an automatic reboot replay recipe. Cap queued runs and concurrent endpoints through config, report queue-full explicitly.

**Step 4 — verify GREEN:** Run .venv/bin/python -m pytest tests/test_repair_scheduler.py -q with fake UTC clock. Include DST, revoked approvals between batches, disabled engineer, cancellation races and unrelated evidence refresh remaining valid.

**Step 5 — checkpoint:** review only the listed file changes, stage deliberately, commit the verified task, update its status and the progress counts.

### Task 15: Add provider-neutral approval requests with a safe fallback 🟢 COMPLETED

**Execution 2026-11-08:** Implemented provider-neutral review requests and workspace human decisions. No AI approval tool; removed four legacy fleet MCP execution tools that bypassed the workspace decision.

**Files:**
- Create: `/Users/cal/remote-fleet-agent/src/remote_fleet_agent/approval_requests.py`
- Modify: `/Users/cal/remote-fleet-agent/src/remote_fleet_agent/support_mcp.py`
- Modify: `/Users/cal/remote-fleet-agent/src/remote_fleet_agent/live_app.py`
- Modify: `/Users/cal/remote-fleet-agent/src/remote_fleet_agent/app_assets/operations.js`
- Create: `/Users/cal/remote-fleet-agent/tests/test_approval_requests.py`
- Modify: `/Users/cal/remote-fleet-agent/tests/test_support_mcp.py`

**Behavioral contract:**

| Input | Expected output |
|-------|-----------------|
| Any MCP client requests decision | Plan digest plus workspace review URL; no authority grant |
| Tool argument approved=true or user_approved text | Ignored/rejected as human decision |
| Authenticated engineer approves current digest in workspace | One consumed decision for that purpose/scope |
| Nonce replay, changed scope or expired request | Denied |
| Provider lacks trustworthy human event | Workspace fallback remains usable |

**Language Rules (Python):** use the project virtual environment and pytest; write the failing test first, verify the intended failure, implement, then verify PASS. Add public-function type hints. Run the configured Ruff checks. No type checker is configured today; record that rather than claiming a mypy pass. Prefer stdlib and lazy imports for optional dependencies. Raise specific actionable exceptions. Declare test dependencies in pyproject.toml and record installed versions in test evidence. Keep secrets outside committed files.

**Step 1 — RED:** add the following test, plus the boundary cases above. Include the relevant pytest/imports and the fixture described by this task.

```python
async def test_ai_cannot_self_approve(approval_env):
    req = approval_env.request()
    with pytest.raises(Denied):
        approval_env.decide_as_technician(req, approved=True)
    assert approval_env.dispatch_count == 0
```

**Step 2 — verify RED:** run `.venv/bin/python -m pytest /Users/cal/remote-fleet-agent/tests/test_approval_requests.py::test_ai_cannot_self_approve -v`; expect failure for the missing contract after the fixture/import scaffolding exists.

**Step 3 — GREEN:**

Use existing engineer session authentication as the first proven human channel. Request record binds purpose (target_confirmation/repair/readiness), actor, client/targets, digest, nonce, expiry and consumed state. Decision requires currently authorised human session, exact digest/revision and atomic single-use consumption. Return review URL without tokens. Technician MCP exposes request/status only. An in-client approval adapter may be enabled only when its actual SDK/protocol yields an engineer-authenticated event inaccessible to model tool arguments; maintain an explicit capability table. Generic MCP elicitation, a client tool-confirmation popup or model transcript text by itself does not establish identity or binding. Ship unsupported providers with workspace fallback and test that fallback through their standard stdio tools. Native in-client approval remains capability-gated and pending until a provider-specific implementation and negative tests exist; do not invent a universal trusted callback endpoint.

**Step 4 — verify GREEN:** Run .venv/bin/python -m pytest tests/test_approval_requests.py tests/test_support_mcp.py -q. Remove exact seven-tool assumption from old tests while continuing to assert no AI decision tools are exposed.

**Step 5 — checkpoint:** review only the listed file changes, stage deliberately, commit the verified task, update its status and the progress counts.

### Task 16: Render new investigation, plan and rollout states in the workspace 🟢 COMPLETED

**Execution 2026-11-08:** Implemented investigation/repair review pages, exact target/scripts/windows, readiness, cancellation and guarded reconciliation; simulated workflow captured.

**Files:**
- Modify: `/Users/cal/remote-fleet-agent/src/remote_fleet_agent/operations.py`
- Modify: `/Users/cal/remote-fleet-agent/src/remote_fleet_agent/app_assets/operations.js`
- Modify: `/Users/cal/remote-fleet-agent/src/remote_fleet_agent/app_assets/app.css`
- Create: `/Users/cal/remote-fleet-agent/tests/browser/test_administration_review.py`

**Behavioral contract:**

| Input | Expected output |
|-------|-----------------|
| Multi-platform plan | Exact platform steps and full target set inspectable |
| Canary paused | Readiness decision visible to authorised engineer |
| Approval expired or new revision | Execution disabled, reason and new-review action shown |
| Unknown step | Affected target and last known dispatch shown, no blind retry |
| Case manually reconciled | Who/when/evidence retained; prior uncertainty preserved |

**Language Rules (Python):** use the project virtual environment and pytest; write the failing test first, verify the intended failure, implement, then verify PASS. Add public-function type hints. Run the configured Ruff checks. No type checker is configured today; record that rather than claiming a mypy pass. Prefer stdlib and lazy imports for optional dependencies. Raise specific actionable exceptions. Declare test dependencies in pyproject.toml and record installed versions in test evidence. Keep secrets outside committed files.

**Step 1 — RED:** add the following test, plus the boundary cases above. Include the relevant pytest/imports and the fixture described by this task.

```python
def test_approval_names_every_target(page, staged_case_url, signed_in):
    page.goto(staged_case_url)
    for name in ("DESKTOP-6PB6U6S", "linux-a", "mac-local"):
        expect(page.get_by_test_id("repair-targets")).to_contain_text(name)
    expect(page.get_by_text("Trial device", exact=True)).to_be_visible()
```

**Step 2 — verify RED:** run `.venv/bin/python -m pytest /Users/cal/remote-fleet-agent/tests/browser/test_administration_review.py::test_approval_names_every_target -v`; expect failure for the missing contract after the fixture/import scaffolding exists.

**Step 3 — GREEN:**

Extend the already shipped case page rather than building another review surface. Add session account/identity, explicit step list, allowed branches, package version/hash, trial/batch membership, stop policy, disruption/readiness and time windows. Render unavailable provider approval as Open engineer review. Show both target decisions and plan decisions in Approvals with their purpose. Reconciliation requires scoped engineer permission, revision and recorded observation; it never rewrites the old execution result. A fresh repair is a new plan/approval. Check all mutating endpoints server-side; UI action availability comes from capability and current-state data. Legacy v0.9 cases retain their original review layout and semantics.

**Step 4 — verify GREEN:** Run .venv/bin/python -m pytest tests/browser/test_administration_review.py tests/browser/test_case_review.py -q. Cover keyboard-only decisions, full target visibility, mobile view, old cases and stale refresh.

**Step 5 — checkpoint:** review only the listed file changes, stage deliberately, commit the verified task, update its status and the progress counts.

### Task 17: Make controller lifecycle and scheduling durable 🟢 COMPLETED

**Execution 2026-11-08:** Implemented scheduler lease, queue ceiling, startup claims, lifecycle health, bounded drain and backup. Service templates supplied; not installed on this host.

**Files:**
- Modify: `/Users/cal/remote-fleet-agent/src/remote_fleet_agent/live_app.py`
- Modify: `/Users/cal/remote-fleet-agent/src/remote_fleet_agent/live_service.py`
- Modify: `/Users/cal/remote-fleet-agent/src/remote_fleet_agent/repair_scheduler.py`
- Create: `/Users/cal/remote-fleet-agent/tests/test_controller_lifecycle.py`
- Modify: `/Users/cal/remote-fleet-agent/docs/DEPLOYMENT.md`

**Behavioral contract:**

| Input | Expected output |
|-------|-----------------|
| AI client disconnects | Approved execution continues on running controller |
| Controller restarts before queued step | Pending work revalidated before dispatch |
| Restart during dispatch | Unknown and reconciliation required; no replay |
| Second controller tries scheduler ownership | Read-only UI allowed; one scheduler lease owner |
| Shutdown | Stop accepting runs, bounded drain, persist unfinished work |

**Language Rules (Python):** use the project virtual environment and pytest; write the failing test first, verify the intended failure, implement, then verify PASS. Add public-function type hints. Run the configured Ruff checks. No type checker is configured today; record that rather than claiming a mypy pass. Prefer stdlib and lazy imports for optional dependencies. Raise specific actionable exceptions. Declare test dependencies in pyproject.toml and record installed versions in test evidence. Keep secrets outside committed files.

**Step 1 — RED:** add the following test, plus the boundary cases above. Include the relevant pytest/imports and the fixture described by this task.

```python
async def test_controller_restart_does_not_replay_dispatched_step(lifecycle_env):
    lifecycle_env.persist_dispatch_intent()
    await lifecycle_env.restart()
    assert lifecycle_env.step_state == "unknown"
    assert lifecycle_env.ssh_calls == []
```

**Step 2 — verify RED:** run `.venv/bin/python -m pytest /Users/cal/remote-fleet-agent/tests/test_controller_lifecycle.py::test_controller_restart_does_not_replay_dispatched_step -v`; expect failure for the missing contract after the fixture/import scaffolding exists.

**Step 3 — GREEN:**

Store queued schedules, readiness and cancellation decisions durably in SQLite; use a scheduler-owner lease separate from endpoint claims. A lease expiry allows a new scheduler to inspect state, not to replay uncertain steps. Provide authenticated /api/health with liveness, scheduler readiness, schema version, queue pressure and integrity status; no keys/paths. Log startup, ready, state transitions, draining and shutdown-complete with run/case/client correlation ids. Use measured duration and queue-depth counters. Keep localhost binding as default. The first always-on deployment runs the same controller on a dedicated Windows/macOS/Linux host with engineer access via a verified SSH local-forward and existing application login; this preserves the loopback MCP bridge contract. Document keys and OS credential storage, backup/restore, startup service templates, log retention and resource bounds. Direct public HTTP/TLS, vendor cloud and multi-node distributed execution are deferred. Never expose the current loopback app with an unrestricted bind flag as a shortcut.

**Step 4 — verify GREEN:** Run .venv/bin/python -m pytest tests/test_controller_lifecycle.py tests/test_repair_scheduler.py -q. Verify lifecycle order and absence of credentials/scripts from structured logs.

**Step 5 — checkpoint:** review only the listed file changes, stage deliberately, commit the verified task, update its status and the progress counts.

### Task 18: Create an explicit native validation harness 🟢 COMPLETED

**Execution 2026-11-08:** Implemented opt-in native manifest harness, sentinel/scratch/process safety checks and separate human approval. Guard tests pass; native tests skip without prerequisites.

**Files:**
- Create: `/Users/cal/remote-fleet-agent/tests/native/conftest.py`
- Create: `/Users/cal/remote-fleet-agent/tests/test_native_harness.py`
- Create: `/Users/cal/remote-fleet-agent/tests/native/test_support_native.py`
- Create: `/Users/cal/remote-fleet-agent/tests/native/test_controller_native.py`
- Create: `/Users/cal/remote-fleet-agent/scripts/validate-support.py`
- Modify: `/Users/cal/remote-fleet-agent/pyproject.toml`
- Create: `/Users/cal/remote-fleet-agent/docs/NATIVE-SUPPORT-TESTING.md`

**Behavioral contract:**

| Input | Expected output |
|-------|-----------------|
| Native tests invoked without --run-native | Skipped and no connections |
| Manifest target mismatch or missing host key | Fail preflight before scratch creation |
| Real platform scratch repair | Separate human approval, exact script, check, recovery and cleanup recorded |
| Cleanup verification fails | Failed/unknown cleanup retained with path; no recursive broad deletion |

**Language Rules (Python):** use the project virtual environment and pytest; write the failing test first, verify the intended failure, implement, then verify PASS. Add public-function type hints. Run the configured Ruff checks. No type checker is configured today; record that rather than claiming a mypy pass. Prefer stdlib and lazy imports for optional dependencies. Raise specific actionable exceptions. Declare test dependencies in pyproject.toml and record installed versions in test evidence. Keep secrets outside committed files.

**Step 1 — RED:** add the following test, plus the boundary cases above. Include the relevant pytest/imports and the fixture described by this task.

```python
@pytest.mark.native
@pytest.mark.parametrize("platform", ["windows", "linux", "macos"])
async def test_scratch_repair_requires_human_approval(native_lab, platform):
    lab = native_lab.for_platform(platform)
    case = await lab.propose_scratch_repair()
    await lab.assert_unapproved_execution_denied(case)
    await lab.wait_for_engineer_decision(case)
    result = await lab.execute(case)
    assert result["check_passed"] is True
    assert result["user_outcome"] == "pending"
```

**Step 2 — verify RED:** run `.venv/bin/python -m pytest /Users/cal/remote-fleet-agent/tests/native/test_support_native.py::test_scratch_repair_requires_human_approval -v`; expect failure for the missing contract after the fixture/import scaffolding exists.

**Step 3 — GREEN:**

Add an opt-in pytest marker and --run-native flag. Load a private manifest from REMOTE_FLEET_NATIVE_MANIFEST only with that flag. Manifest fields: workspace directory, expected client/device/platform/account/port/public host-key fingerprint, allowed scratch parent, current authorisation reference and expiry policy; key/token references are private file paths, never contents. Compare manifest to registered scope and actual pinned SSH identity. Harness prints the exact scope before changes and produces engineer review links. Native tests have no owner-token fixture that automatically approves changes; human approval is a distinct recorded gate. Unit/simulated tests may use in-memory owner fixtures.

Use a random run id and an endpoint-created scratch path under the account's temp directory, with a sentinel containing that run id. Every write, process and cleanup validates the sentinel and resolves the exact child path. A small file fixture starts as 'broken', an approved repair changes it to 'healthy', JSON verification checks exact content, and approved recovery restores 'broken'. A separate disposable process fixture tests terminating/restarting only a process whose executable/run-id/start-time match. On Windows use Test-Path/Get-Content/Set-Content and explicit native exit handling; on macOS/Linux POSIX shell with Python3 for safe JSON and paths. Do not use Outlook, print queues, system services or networking for the generic smoke. Native outage injection uses a lab-only SSH shim or disposable Linux container, never disabling the Mac's network. scripts/validate-support.py supports preflight, prepare, run and cleanup subcommands with argparse; every mutation goes through the support plan workflow, not direct helper SSH.

**Step 4 — verify GREEN:** Run .venv/bin/python -m pytest tests/native -q (all skipped). Unit-test preflight/manifest/cleanup guards in tests/test_native_harness.py before running .venv/bin/python -m pytest tests/native --run-native -q. A missing native prerequisite is not a pass.

**Step 5 — checkpoint:** review only the listed file changes, stage deliberately, commit the verified task, update its status and the progress counts.

### Task 19: Validate Linux execution with two disposable Docker devices ⚪ BLOCKED

**Execution 2026-11-08:** Lab implemented and two real pinned SSH Linux devices collected inventory/diagnostics/readiness; deliberate bad-pin rejection and isolation verified. Approved scratch/canary/outage native cycles NOT_RUN without a separate human decision. Project-specific lab teardown PASS.

**Files:**
- Modify: `/Users/cal/remote-fleet-agent/src/remote_fleet_agent/docker_lab.py`
- Modify: `/Users/cal/remote-fleet-agent/src/remote_fleet_agent/lab_assets/Dockerfile`
- Modify: `/Users/cal/remote-fleet-agent/tests/test_cross_platform.py`
- Create: `/Users/cal/remote-fleet-agent/tests/native/test_linux_docker.py`
- Modify: `/Users/cal/remote-fleet-agent/docs/CROSS-PLATFORM-LAB.md`

**Behavioral contract:**

| Input | Expected output |
|-------|-----------------|
| New lab initialised | Two loopback-only SSH endpoints; isolated writable /tmp scratch; root filesystem remains read-only |
| Approved scratch change/recovery | Exact content transitions verified over real SSH |
| First target fails check | Second target unchanged |
| Container disconnect after send | Unknown, no replay |
| Lab down | Only this project containers/network removed |

**Language Rules (Python):** use the project virtual environment and pytest; write the failing test first, verify the intended failure, implement, then verify PASS. Add public-function type hints. Run the configured Ruff checks. No type checker is configured today; record that rather than claiming a mypy pass. Prefer stdlib and lazy imports for optional dependencies. Raise specific actionable exceptions. Declare test dependencies in pyproject.toml and record installed versions in test evidence. Keep secrets outside committed files.

**Step 1 — RED:** add the following test, plus the boundary cases above. Include the relevant pytest/imports and the fixture described by this task.

```python
@pytest.mark.native
async def test_linux_canary_stops_second_device(native_lab):
    lab = native_lab.linux_pair()
    run = await lab.approved_failing_canary()
    await lab.wait_finished(run)
    assert await lab.read_marker("linux-b") == "broken"
    assert lab.result(run)["state"] == "paused"
```

**Step 2 — verify RED:** run `.venv/bin/python -m pytest /Users/cal/remote-fleet-agent/tests/test_cross_platform.py::test_linux_canary_stops_second_device -v`; expect failure for the missing contract after the fixture/import scaffolding exists.

**Step 3 — GREEN:**

The current Docker lab already has writable /tmp tmpfs; use /tmp/remote-fleet-support-<run-id> owned by fleet. Expand the fixture to include a controllable unprivileged disposable worker and file state only if necessary. Keep read_only, resource limits, no-new-privileges, lab keys, pinned host keys, local-engine requirement and loopback port publishing. Do not mount the Docker socket or host home. Recompute controlled-template validation for the new version and require a new empty lab directory. Build and run via the existing lab init/up/status/down commands. Register that lab as its own authorised workspace client with administration opt-in, not as a real customer. Test Unix permissions denial, simultaneous stdout/stderr, long output, timeout, service-like process recovery and two-device staging. Record systemd, kernel, physical disk, GUI and package-manager service tests as outside container evidence.

**Step 4 — verify GREEN:** Define LAB_DIR as a newly created private empty temporary directory and retain its exact path in the report; run .venv/bin/remote-fleet-agent lab init --directory "$LAB_DIR", then lab up/status. Set native manifest and run .venv/bin/python -m pytest tests/native/test_linux_docker.py --run-native -q. Finish with lab down for that directory; never docker system prune.

**Step 5 — checkpoint:** review only the listed file changes, stage deliberately, commit the verified task, update its status and the progress counts.

### Task 20: Validate the enrolled Windows test PC ⚪ BLOCKED

**Execution 2026-11-08:** Real Windows pinned SSH preflight PASS: DESKTOP-6PB6U6S, PowerShell 5.1, sshd running, ~9.7 GB free. Current expanded-administration enrollment/manifest and human decision missing; new scratch/recovery/GUI repair cycle BLOCKED.

**Files:**
- Create: `/Users/cal/remote-fleet-agent/tests/native/test_windows_support.py`
- Modify: `/Users/cal/remote-fleet-agent/docs/NATIVE-SUPPORT-TESTING.md`

**Behavioral contract:**

| Input | Expected output |
|-------|-----------------|
| Preflight DESKTOP-6PB6U6S | Current address/key/account/space/OpenSSH/access policy recorded |
| OpenSSH already installed | Reuse service; no reinstall during smoke test |
| Approved scratch repair and recovery | Real PowerShell results and exact marker content recorded |
| Wrong desktop/session context | Displayed explicitly; no claim Outlook opened |
| Missing/expired test installation | Blocked with reason; existing cleanup/expiry retained |

**Language Rules (Python):** use the project virtual environment and pytest; write the failing test first, verify the intended failure, implement, then verify PASS. Add public-function type hints. Run the configured Ruff checks. No type checker is configured today; record that rather than claiming a mypy pass. Prefer stdlib and lazy imports for optional dependencies. Raise specific actionable exceptions. Declare test dependencies in pyproject.toml and record installed versions in test evidence. Keep secrets outside committed files.

**Step 1 — RED:** add the following test, plus the boundary cases above. Include the relevant pytest/imports and the fixture described by this task.

```python
@pytest.mark.native
async def test_windows_scratch_repair_and_recovery(native_lab):
    lab = native_lab.for_platform("windows")
    assert lab.expected_device == "DESKTOP-6PB6U6S"
    result = await lab.run_approved_scratch_cycle()
    assert result["change_content"] == "healthy"
    assert result["recovery_content"] == "broken"
    assert result["cleanup_verified"] is True
```

**Step 2 — verify RED:** run `.venv/bin/python -m pytest /Users/cal/remote-fleet-agent/tests/native/test_windows_support.py::test_windows_scratch_repair_and_recovery -v`; expect failure for the missing contract after the fixture/import scaffolding exists.

**Step 3 — GREEN:**

Resolve the Windows machine from the current private enrollment; screenshots from 7 November do not establish today's IP or remaining access. Verify fingerprint against pinned trusted identity, account privileges, free space, PowerShell version and service capability without installing anything. Run custom diagnostic identity/process metadata, negative unapproved repair, stale/revoked approval, approved scratch change/check/recovery, no-op and bounded-output cases. Start a dedicated test process only if needed; validate run id, PID and start time before changing it. Test Windows as controller host separately by running the Python service/tests and SDK stdio bridge on that PC with its own private workspace. Mac-to-Windows target success alone does not validate a Windows controller. Native Outlook process repair and real printer-driver reinstallation remain separate supervised scenario gates with exact affected session/queue/package and user confirmation; never manufacture those faults to satisfy this smoke suite. Record those scenarios NOT_RUN until actually demonstrated.

**Step 4 — verify GREEN:** Run .venv/bin/python -m pytest tests/native/test_windows_support.py --run-native -q on the engineer host. On Windows controller use py -3 -m venv .venv and .venv\Scripts\python.exe -m pytest tests/test_operations.py tests/test_support.py tests/test_support_mcp.py -q; adapt repo location explicitly.

**Step 5 — checkpoint:** review only the listed file changes, stage deliberately, commit the verified task, update its status and the progress counts.

### Task 21: Validate this native Mac and controller-host portability ⚪ BLOCKED

**Controller follow-up 2026-11-09:** Native Windows/macOS/Linux CI passed on Python 3.11 and 3.13, including package builds; see [verified matrix](https://github.com/maccydee/remote-fleet-agent/actions/runs/42546966870). This clears the Windows controller gate. Native Mac SSH and the enrolled-target repair workflow remain blocked.

**Execution 2026-11-08:** macOS controller tests PASS; actual installed wheel 83 tests PASS. Native Mac target TCP22 refuses SSH, so native repair BLOCKED. Linux installed controller 83 tests PASS; Windows controller NOT_RUN locally. CI matrix supplied, not claimed executed.

**Files:**
- Create: `/Users/cal/remote-fleet-agent/tests/native/test_macos_support.py`
- Modify: `/Users/cal/remote-fleet-agent/tests/native/test_controller_native.py`
- Modify: `/Users/cal/remote-fleet-agent/.github/workflows/compatibility.yml`
- Modify: `/Users/cal/remote-fleet-agent/docs/NATIVE-SUPPORT-TESTING.md`

**Behavioral contract:**

| Input | Expected output |
|-------|-----------------|
| Mac SSH already available with pinned identity | Native shell scratch cycle verified |
| Remote Login unavailable | Record blocked; enabling it is a separate explicit system change |
| Mac account lacks elevated privileges | Accurate limitation; no sudo fallback |
| macOS/Linux/Windows controller matrix | Platform-specific suite result recorded independently |
| Linux controller in Docker | Label container-host evidence; native OS-service installation remains separate |

**Language Rules (Python):** use the project virtual environment and pytest; write the failing test first, verify the intended failure, implement, then verify PASS. Add public-function type hints. Run the configured Ruff checks. No type checker is configured today; record that rather than claiming a mypy pass. Prefer stdlib and lazy imports for optional dependencies. Raise specific actionable exceptions. Declare test dependencies in pyproject.toml and record installed versions in test evidence. Keep secrets outside committed files.

**Step 1 — RED:** add the following test, plus the boundary cases above. Include the relevant pytest/imports and the fixture described by this task.

```python
@pytest.mark.native
async def test_native_mac_reports_real_identity(native_lab):
    lab = native_lab.for_platform("macos")
    evidence = await lab.confirmed_diagnostics()
    assert evidence["platform"] == "macos"
    assert evidence["simulation"] is False
    assert evidence["kernel"] == "Darwin"
```

**Step 2 — verify RED:** run `.venv/bin/python -m pytest /Users/cal/remote-fleet-agent/tests/native/test_macos_support.py::test_native_mac_reports_real_identity -v`; expect failure for the missing contract after the fixture/import scaffolding exists.

**Step 3 — GREEN:**

Use this Mac as an enrolled loopback SSH target only if its existing Remote Login and pinned account access are available; local subprocess success is not a substitute for SSH execution. Keep the controller repo/workspace outside the scratch target, and use only the dedicated scratch/process fixtures. Verify diagnostic context, approved change/check, pre-approved recovery, stale plan rejection, cleanup and evidence retention. Test actual macOS shell behaviour, paths with spaces/unicode, restricted account failure and SSH session versus GUI session. Run controller service, HTTP, SDK stdio and wheel smoke on this Mac. Extend existing compatibility CI for unit/API tests on three native runner OSes, without exposing test-PC credentials. Run Linux controller/wheel in a separate container when native Linux isn't available; label that evidence clearly. Do not claim all 3x3 controller-target combinations from one Mac driving three endpoints.

**Step 4 — verify GREEN:** Run .venv/bin/python -m pytest tests/native/test_macos_support.py --run-native -q. Keep a controller-host x target-platform matrix with PASS/FAIL/BLOCKED/NOT_RUN, execution date and artifact links.

**Step 5 — checkpoint:** review only the listed file changes, stage deliberately, commit the verified task, update its status and the progress counts.

### Task 22: Verify AI clients, release evidence and upgrade behaviour ⚪ BLOCKED

**Execution 2026-11-08:** Final source suite 382 PASS/11 SKIP; browser 26 PASS; installed Mac/Linux 83 PASS each; Ruff/JS syntax PASS. Seven provider config formats and actual SDK stdio verified. Interactive Claude/Codex/Gemini sessions NOT_RUN; full native release gate remains blocked.

**Files:**
- Modify: `/Users/cal/remote-fleet-agent/tests/test_support_mcp.py`
- Modify: `/Users/cal/remote-fleet-agent/tests/test_integration_compatibility.py`
- Modify: `/Users/cal/remote-fleet-agent/docs/AI-AND-HOST-COMPATIBILITY.md`
- Modify: `/Users/cal/remote-fleet-agent/docs/AI-SUPPORT.md`
- Create: `/Users/cal/remote-fleet-agent/docs/SUPPORT-RELEASE-GATES.md`
- Modify: `/Users/cal/remote-fleet-agent/README.md`

**Behavioral contract:**

| Input | Expected output |
|-------|-----------------|
| Generic SDK stdio workflow | Investigate/propose/wait/execute/read results with separate human approval |
| Claude/Codex/Gemini configs | Generated on that host; unrelated settings untouched |
| Provider not interactively tested | Config generated / SDK tested / interactive NOT_RUN distinctions |
| Old v0.9 workspace upgrade | Records and original approvals preserved; no expanded authority |
| Release report | Actual tests, skips, native gaps and recovery evidence linked |

**Language Rules (Python):** use the project virtual environment and pytest; write the failing test first, verify the intended failure, implement, then verify PASS. Add public-function type hints. Run the configured Ruff checks. No type checker is configured today; record that rather than claiming a mypy pass. Prefer stdlib and lazy imports for optional dependencies. Raise specific actionable exceptions. Declare test dependencies in pyproject.toml and record installed versions in test evidence. Keep secrets outside committed files.

**Step 1 — RED:** add the following test, plus the boundary cases above. Include the relevant pytest/imports and the fixture described by this task.

```python
async def test_stdio_cannot_claim_human_decision(mcp_session):
    tools = (await mcp_session.list_tools()).tools
    assert not any(t.name in {"approve_repair", "approve_investigation"} for t in tools)
    result = await mcp_session.call_tool("request_repair_approval", {
        "client_id": "northstar-demo", "case_id": "fixture-case"})
    assert not result.is_error
    # Request provides review link and pending status, never an approved decision.
```

**Step 2 — verify RED:** run `.venv/bin/python -m pytest /Users/cal/remote-fleet-agent/tests/test_support_mcp.py::test_stdio_cannot_claim_human_decision -v`; expect failure for the missing contract after the fixture/import scaffolding exists.

**Step 3 — GREEN:**

Update tests to the actual final tool schemas; preserve SDK stdio process startup and authenticated loopback HTTP coverage. Test provider-generated configs with spaces in interpreter/token paths on all hosts. Perform one real interactive read/investigate/propose/review/execute/result cycle in each installed AI client using lab scope; install or alter client settings only when separately authorised, otherwise record NOT_RUN. Review links must route to the same canonical case and preserve state after login. Upgrade a copied v0.9 workspace, verify archive reads and legacy-digest behaviour, then rehearse backup restore into a new directory. No automatic execution of old approvals on upgrade. Final release checks include complete pytest suite, browser tests, Ruff, clean installed-wheel smoke, dependency/version report, three endpoint evidence reports, controller matrix, known limitations and no-secret artifact review. Update marketing only with demonstrated features and clearly simulated screenshots. Version/release publication remains a separate action.

**Step 4 — verify GREEN:** Run .venv/bin/python -m pytest -q; .venv/bin/python -m ruff check src tests; .venv/bin/python -m pytest tests/browser -q. Native commands run separately with explicit flag/manifest. Record exact commands, counts, versions, date, source revision/diff and remaining NOT_RUN items.

**Step 5 — checkpoint:** review only the listed file changes, stage deliberately, commit the verified task, update its status and the progress counts.

### Task 23: Capture verified workflows for marketing ⚪ BLOCKED

**Execution 2026-11-08:** Capture tooling, privacy manifest and gallery implemented. Fresh simulated workflow: 10 inspected PNGs and fully played 23.72s browser recording. No native/provider footage claimed; those shots depend on blocked tasks 19–22. Publication review pending; zero public assets.

**Files:**
- Create: `/Users/cal/remote-fleet-agent/docs/MARKETING-CAPTURE.md`
- Create: `/Users/cal/remote-fleet-agent/scripts/capture-showcase.py`
- Create: `/Users/cal/remote-fleet-agent/tests/test_marketing_capture.py`
- Modify: `/Users/cal/remote-fleet-agent/tests/browser/conftest.py`
- Modify: `/Users/cal/remote-fleet-agent/docs/SUPPORT-RELEASE-GATES.md`

**Timing:** establish the capture convention before Task 8 browser validation. Capture new scenes during Tasks 8, 16 and 19–22, then finish this task by reviewing and indexing the complete set. A test milestone should produce its usable footage while the environment is already prepared.

**Language Rules (Python):** use pytest and the project virtual environment; verify RED before GREEN. Type public functions, run Ruff, prefer stdlib, lazy-import optional browser dependencies and raise actionable exceptions. Record capture-tool versions. Keep credentials and native evidence outside committed files.

**Behavioral contract:**

| Input | Expected output |
|-------|-----------------|
| Verified fictional browser scenario with capture enabled | PNG screenshots and a real browser recording, indexed against the test/run |
| Capture disabled | Normal test results with no marketing artifacts |
| Failed or incomplete scenario | Private debugging artifact; no publishable success claim |
| Native test capture | Private by default with actual platform and run provenance |
| Public candidate missing review or containing sensitive content | Withheld from the marketing-ready index |
| Simulated scene | Visible simulation label and simulation metadata retained |

**Step 1 — RED:** in `/Users/cal/remote-fleet-agent/tests/test_marketing_capture.py`, test that the index includes only assets whose files exist, whose associated scenario passed, and whose human review status is approved. Parametrize missing file, failed scenario, missing review, native/private classification and simulation labels. A file being present does not prove its content is safe.

**Step 2 — verify RED:** run `.venv/bin/python -m pytest /Users/cal/remote-fleet-agent/tests/test_marketing_capture.py -v`; expect failures for the missing manifest/index contract after creating import scaffolding.

**Step 3 — GREEN:** add an opt-in `--capture-showcase` browser fixture. Use a fresh isolated browser context with recording enabled before opening the scene; close the context before indexing its completed video. Save PNGs at deliberate workflow checkpoints rather than on every polling update. Use a 1440×900 desktop viewport, plus selected 390×844 screenshots demonstrating narrow-screen usability. Keep source recordings in the capture tool's native format; editing, branded films and publishing are separate follow-up work.

Store assets in a private output directory selected by the operator. Default to a new run-specific directory outside the repository; preserve source recordings separately from reviewed public candidates. A manifest entry has asset path, scene, media type, actual dimensions, source revision/diff hash, UTC capture time, associated test/run id, scenario result, environment (simulation/native), actual platform/provider if applicable, privacy class, review status and claim limitations. Record actual measured recording duration when available; leave unknown values null. Index generation never changes review status automatically.

Capture this shot list as the relevant features pass:

| Scene | Screenshots | Short recording target | Dependency |
|-------|-------------|------------------------|------------|
| Operations across fictional clients | Full overview and filtered attention queue | 15–30 seconds filtering and opening work | Task 8 |
| Device search and platform context | Windows/macOS/Linux rows and one device page | 10–20 seconds choosing the exact device | Task 8 |
| Investigation and repair review | Findings, commands, disruption and targets | 30–60 seconds investigating and reviewing a proposal | Tasks 8/16 |
| Human approval and results | Pending decision, approved plan, checks versus user outcome | 30–60 seconds showing the real decision and result transition | Task 16 |
| Canary stop and recovery | Failure/unknown state and exact recovery record | 20–45 seconds demonstrating bounded rollout behaviour | Tasks 16/19 |
| Provider/terminal workflow | Actual installed AI client using the tool, with review link | 30–60 seconds of conversation → tool result → workspace review | Task 22 |
| Native cross-platform validation | Safe scratch result per actual OS | Separate clips if useful; keep private until reviewed | Tasks 19–21 |

These are target clip lengths, not invented timing guarantees. Keep the existing logo and capture completed working screens. Capture normal scrolling and interactions; a recording must show what actually occurred. Show simulation throughout fictional workflows. Never use synthetic transitions to imply that a native repair occurred.

For public UI footage, use fresh fictional clients, device names and issue text. For terminal/AI-client footage, use a dedicated clean session with lab-only history; capture only the intended window or region. Avoid account menus, notification overlays, token files, enrollment commands containing secrets, private key paths, real customer names and unrelated desktop content. Native recordings remain private until separately inspected and explicitly classified for marketing. Prefer reproducing the same verified workflow in the fictional environment instead of redacting a sensitive native recording. Retain the simulation label.

**Step 4 — verify GREEN:** run `.venv/bin/python -m pytest /Users/cal/remote-fleet-agent/tests/test_marketing_capture.py -q`, then run a passing browser scenario with `--capture-showcase`. Open every selected screenshot and play every selected source clip. Check legibility, cursor/focus, complete start/end, visible simulation state, absence of sensitive content and correspondence to the recorded result. Debug recordings of failures remain in a separate private index. Do not claim footage exists until files have been inspected.

**Step 5 — checkpoint:** commit only capture tooling, tests and documentation. Keep generated recordings, screenshots and manifests out of source control by default. Produce a reviewed contact sheet/index with links to the selected originals. Publishing or uploading is a separate action.

## Reference implementation contracts

Use these building blocks in the named tasks. They define the choices that would otherwise be easy to implement differently.

### Task 4 — prevent an old read overwriting a new route

In `/Users/cal/remote-fleet-agent/src/remote_fleet_agent/app_assets/operations.js`, keep one generation counter for navigation-dependent reads. Invalidate it on navigation, filters, sign-out and client/site changes. Mutation requests use captured case identity and are never silently retried.

```javascript
let readGeneration = 0;
function invalidateReads() { readGeneration += 1; }
async function loadRoute(route, fetchData, renderData, showError) {
  const generation = ++readGeneration;
  const routeKey = JSON.stringify(route);
  try {
    const data = await fetchData(route);
    if (generation !== readGeneration) return;
    renderData(data, routeKey);
  } catch (error) {
    if (generation === readGeneration) showError(error);
  }
}
```

Render functions use the supplied captured route rather than a mutable selected-client variable. For forms, capture client/id/revision/digest on opening the review; scope labels in that review match the captured values. Add a browser race test with intentionally delayed responses: open case A, then B, release A's response last; B stays visible and its buttons target B.

### Tasks 11 and 13 — evaluate only declared check predicates

Add to `/Users/cal/remote-fleet-agent/src/remote_fleet_agent/repair_plan.py`. Validation has already enforced that each referenced step is an earlier `kind=check` step, and that change/recovery branches cannot introduce new code.

```python
from typing import Any

def evaluate_when(
    predicate: dict[str, Any] | None,
    results: dict[str, dict[str, Any]],
) -> bool | None:
    """None means indeterminate: pause instead of selecting a branch."""
    if predicate is None:
        return True
    if set(predicate) != {"step", "field", "equals"}:
        raise ValueError("when requires step, field and equals")
    if not isinstance(predicate["step"], str) or not predicate["step"]:
        raise ValueError("when.step is required")
    field = predicate["field"]
    expected = predicate["equals"]
    if field == "passed":
        if type(expected) is not bool:
            raise ValueError("when.equals must be bool for passed")
    elif field == "exit_code":
        if type(expected) is not int:
            raise ValueError("when.equals must be int for exit_code")
    else:
        raise ValueError("when.field must be passed or exit_code")
    result = results.get(predicate["step"])
    if result is None or result.get("outcome") != "known":
        return None
    if result.get("truncated") is True:
        return None
    observed = result.get(field)
    if type(observed) is not type(expected):
        return None
    return observed == expected
```

Add exact tests: a missing result -> None; outcome=unknown -> None; passed="true" -> None; passed=True with equals=True -> True; passed=False with equals=True -> False; exit_code=0 with equals=False -> ValueError; truncated output -> None. A false condition records a skipped step; an indeterminate condition pauses the run. Recovery checks must have been approved as executable steps, and their prerequisites must be established before any recovery change is sent. A package-dependent change references a reviewed prerequisite check that verifies the exact approved package version/hash; the executable repair rechecks bytes immediately before use. Package metadata alone is not enforcement.

### Tasks 12 and 17 — crash states

| Persisted phase on restart | Meaning | Scheduler action |
|----------------------------|---------|------------------|
| pending, with no dispatch intent | No command sent by this step | Revalidate authority, conditions and window before claiming |
| dispatch_intent or running | Remote completion cannot be established | Mark unknown, retain exclusion and pause |
| known_completed | Result saved | Evaluate the next reviewed condition; never rerun that step |
| known_failed | Failure saved | Evaluate approved recovery only if its conditions hold |
| unknown | Requires reconciliation | Offer engineer investigation, preserve prior result |

A controller lease may expire, but an endpoint's unknown dispatch does not become pending. A manual reconciliation records observation and engineer identity; it does not edit the old result into success. If a safe new action is needed, propose a new plan.

### Fixture names used in tasks 9–22

Create small fixture wrappers around the real new services. `investigation_env` exposes confirmed_session/change_endpoint/validate_session; `diag_env` exposes run and a fake SSH call log; `execution_env` exposes controllers/claim/transport and reloads the real SQLite store; `scheduler_env` uses an injected clock and real scheduler with fake transport; `approval_env` uses real roles and decision requests. The fixture may arrange inputs and inspect persisted results, but it cannot implement the behaviour under test.

`native_lab` loads the private manifest, calls the real controller API and waits for recorded engineer decisions. Its read_marker operation is a confirmed diagnostic call. Its scratch mutations are reviewed plans. The wrapper never substitutes a fake result for an endpoint command or uses an owner token to approve its own plans.

## Finish and handoff

Keep the original audit features reachable, document renamed locations and retain old case links. A UI milestone can ship before administration expansion, with capability checks hiding incomplete controls. The expanded admin release requires the recorded enforcement tests and endpoint matrix; a blocked test is documented, never reported as a pass.

Save the execution log and evidence index beside this plan when implementation starts. The user's instruction is to finish the design and plan without more design questions, so this planning handoff does not request another choice. Implementation is the next stage; this document has not modified endpoints, provider settings or the running workspace.

## Baseline verified during planning

On 8 November 2026, the existing repository suite completed with **250 passed, 1 skipped in 13.84s** using `PYTHONDONTWRITEBYTECODE=1 .venv/bin/python -m pytest -q -p no:cacheprovider`. The first sandboxed run could not bind local test sockets; rerunning with permission for the loopback HTTP/SSH fixtures resolved those environment errors. The tests used existing simulated/fake endpoints, not Cal's Windows PC or native Mac repairs. No future plan task is complete on this evidence.

Planning checks also verified 22 numbered NOT_STARTED tasks, matching progress totals, Python language rules in every task, and syntax parsing of all 24 embedded Python examples. The UI design passed the prose check; the technical plan was reviewed for scope, approval and failure-state consistency. Native tests and the redesigned UI remain NOT_RUN until implementation.

## Marketing capture addition

The user requested screenshots and video during testing. Task 23 was added after the original 22-task planning validation. Capture is integrated into the testing milestones, with fictional public scenes, private native evidence and a reviewed asset index. The plan now has 23 tasks; no screenshots or recordings for the future UI are claimed as captured.

## Execution checkpoint — batch 1, 8 November 2026

Tasks 1–3 implemented and independently verified. Full suite: 298 passed, 1 skipped in 25.79s. Changed code/tests pass Ruff; full-tree Ruff also reports four unrelated pre-existing import-order violations, confirmed unchanged against the baseline. No endpoint changes or provider settings changes. Visible UI work starts at task 4. Commits are held for the executing-plans batch review; batch-only diff and computed example output are saved under the Codex task workspace/work/operations-execution.
