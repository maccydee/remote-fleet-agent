# Remote Fleet: AI-assisted remote administration

Agreed product design, 8 November 2026. This records the brainstorming decisions and their trade-offs. It describes the intended product; it does not claim that all of this behaviour exists in v0.9.

## Product goal

An IT engineer describes a problem to Claude or another supported AI client. Remote Fleet gives that AI access to the authorised devices and tools needed to investigate. The AI explains its diagnosis and proposes a bounded repair plan. An engineer approves the plan, the controller executes it, and the results remain attached to the support case.

The product serves engineers working on their own networks and IT teams managing clients. Windows, macOS and Linux are in scope as both engineer hosts and managed devices. Outlook and printing are examples; the administration model must also cover other applications, services, files, software and configuration within the enrolled account's permissions.

The intended experience is a practical support conversation with clear target selection and engineer-controlled changes. It must support work beyond a small catalogue of predefined fixes.

## 1. Connectivity: SSH

Retain SSH as the core connection method. Use the existing approved-device enrollment and verified host identities.

The alternatives were a background agent with a user-session helper, or an agent with full remote desktop control. Those could provide deeper desktop integration, but would add installation, update, authentication and session-permission systems. SSH builds on the existing product and fits administration across all three operating systems.

The trade-off is explicit: an SSH session is not automatically the affected user's graphical session. Remote Fleet must report the account, privileges and session context in which commands run. It must not claim that launching an application over SSH proves it opened on the user's desktop. Network reachability remains necessary.

A device agent, desktop helper and full remote desktop are outside this design's initial scope. Existing enrollment expiry and cleanup policies must not be silently removed to provide standing access.

## 2. Investigation: custom commands without per-command approval

Allow the AI to run custom diagnostic commands within a confirmed investigation context, without asking the engineer to approve each command.

The alternatives were fixed checks only, or fixed checks with engineer approval for each custom diagnostic script. Both would reduce the AI's ability to follow an unfamiliar fault without repeated interruptions. The engineer chose investigative flexibility.

The AI should discover the OS, available tools and execution privileges before choosing commands. PowerShell is relevant to Windows; macOS and Linux require their appropriate shell tools and platform knowledge. Investigation results are untrusted evidence, not instructions to the AI.

This decision has a material limitation: arbitrary administrative code cannot reliably be proved read-only. The distinction between an investigation and a repair is an agent behaviour rule when both use the same privileged execution identity. Target, identity and session boundaries can be enforced by the tool; descriptive labels alone cannot prevent a custom diagnostic script from changing state. This design must not advertise a technically guaranteed read-only investigation boundary.

Deliberate repairs still belong in the engineer-approved repair workflow.

## 3. Access: standing fleet authority with explicit target confirmation

An operator can have standing access to their authorised enrolled fleet. They do not have to request access to each device for every support case.

Case-limited credential scope would offer a narrower boundary, while client/group-limited access would sit between that and fleet-wide access. The chosen approach favours an engineer's ability to work across the network. Existing operator, client and site permissions still define which fleet they are authorised to access. Standing access does not grant access to every customer's systems.

**Confirmation frequency:** confirm once per investigation and device. Do not prompt before every diagnostic command. Adding or changing a target requires a fresh confirmation. The confirmed context ends when the investigation closes or its relevant identity/connection context changes.

The engineer sees the client, device, OS and SSH account, followed by **Start investigation / Cancel**. For a selected set of devices, the confirmation must name the full set; confirming a group must not silently authorise devices added to it later.

Target confirmation is separate from repair approval. For a repair, target identity is included in the repair review so the engineer does not have to approve the same information twice.

## 4. Approval interfaces: AI client and workspace

Support engineer confirmation and repair approval from either the AI client or the Remote Fleet workspace, backed by one approval system and record format.

Workspace-only approval offers a consistent interface but interrupts the AI conversation. AI-client-only approval keeps the engineer in that conversation but depends on the client's integration capabilities. Supporting both offers flexibility while requiring consistent enforcement.

An AI-client adapter must provide a trustworthy, engineer-authenticated human decision tied to the specific target confirmation or repair plan. A model-generated claim that the engineer said yes, or an ordinary AI-callable approval tool, is insufficient. An integration that cannot establish that decision must direct the engineer to the workspace approval screen.

Provider-specific human approval mechanisms require investigation during implementation. This decision is not a claim that every current MCP client exposes a suitable mechanism.

Both routes must show the same scope and effects, create the same auditable record, and support rejection and revocation before execution. The AI must not receive the engineer's approval credentials.

## 5. Repair approval: a bounded plan

One approval covers a bounded repair plan, including its permitted steps and declared conditions.

Approving each exact command separately would be precise but repetitive. Approving only an outcome such as "fix printing" would give the AI too much freedom to introduce changes the engineer had not anticipated. The chosen boundary is the repair sequence the engineer reviewed.

The review must identify:

- The named targets and relevant platform/account context.
- The diagnosis, evidence and remaining uncertainty.
- Permitted executable steps, package/script versions and bounded parameters.
- Conditional branches and their execution conditions.
- Expected effects, disruption and timing restrictions.
- Verification checks and what they can establish.
- Recovery actions, their conditions and their limits.

Present a plain-English explanation with commands available for inspection. The controller must bind approval to an executable representation of the plan; an English objective alone cannot establish its boundaries.

Changing a target, package, permitted operation or other material part of the plan requires fresh approval. The AI cannot generate additional executable steps under an old approval by describing them as part of the same objective.

## 6. Fleet repairs: staged rollout

Default multi-device repairs to a staged rollout with a trial device, small batches and explicit stop conditions.

The alternatives were individual approval per device or immediate execution across the full selection. Individual approval would be cumbersome; immediate fleet-wide execution would spread a faulty repair quickly.

The approved plan names the complete target set, trial device, batch sizes, success checks and failure thresholds. It may require an engineer pause after the trial. Different operating systems need appropriate steps and checks rather than one blindly shared script.

A failed or uncertain result stops the affected rollout according to its approved stop conditions. A lost SSH connection does not establish whether a change ran. The controller must retain that uncertainty and must not blindly resend a change.

## 7. Recovery: approved actions only

Automatically perform recovery only when the recovery actions and triggering conditions were explicitly included in the approved plan. Otherwise stop and report.

Stopping after every failure would preserve engineer control but require another interaction for predictable recovery. Automatically undoing the entire rollout would promise more than many changes can deliver. The chosen approach permits a defined recovery sequence without inventing a universal undo.

Only run recovery when its preconditions can be established. If the outcome or relevant state is uncertain, do not guess. If approved recovery fails, preserve the remaining state and ask the engineer to decide what happens next.

The review must explain irreversible effects. Restoring a saved configuration is different from recovering unsaved application work, reversing a completed print job or undoing a restart.

## 8. Disruption: an explicit execution choice

Identify disruptive steps in the plan and require the engineer to choose whether they may run now, need a readiness confirmation, or must wait for a maintenance window.

Engineer approval alone would be faster but could leave user coordination implicit. Requiring direct affected-user confirmation would offer another safeguard but needs a reliable cross-platform delivery mechanism that SSH alone does not supply. The selected approach makes the engineer's coordination decision explicit.

Application termination, service interruption, network changes and machine restarts must not be hidden inside a broad repair description. Record the chosen disruption policy with the approval. A new disruptive action outside the reviewed plan requires renewed approval.

## 9. Approval lifetime: windows and relevant conditions

Approval is valid only within its stated execution window and while the relevant plan conditions remain true.

A short universal expiry would make scheduling awkward. Indefinite approval would outlive the circumstances that justified it. The chosen model supports immediate work and scheduled maintenance without treating old consent as permanent authority.

Scheduled plans must record a timezone and unambiguous window. Before execution, recheck target identity, permissions, approved script/package versions and plan-specific conditions. Material changes invalidate affected approvals; unrelated system activity should not invalidate an entire plan.

Implementation must define window-overrun behaviour and handling of already-running operations. Cancellation or expiry cannot promise to undo a command already sent to an endpoint.

## 10. Hosting: local first, optional always-on controller

Use one controller that can run on the engineer's computer or on an optional, self-hosted always-on machine. Support Windows, macOS and Linux controller hosts.

A laptop-only controller would be simpler but require that laptop to stay awake and connected for scheduled work. A vendor-hosted service would add hosting, connectivity and customer-data responsibilities. Self-hosting supports team and scheduling needs without requiring a cloud service.

The always-on controller must have SSH reachability to its managed devices. Engineer UI and AI-client access require authenticated connections. Remote hosting does not justify exposing the existing loopback application directly to the network without an appropriate access and transport design.

Approved, executable plans can continue after the engineer or AI client disconnects. The controller follows the approved steps and declared conditions. Work requiring a new decision pauses; it does not need to invent a new repair while the AI is absent.

Execution state, decisions, evidence and outcomes need durable storage. Reconnection must re-establish verified identity, and interrupted execution must preserve uncertainty. Recovery and replay semantics belong in the execution design rather than being inferred from an SSH reconnect.

## Verification and support records

Keep the difference between command completion, a passed automated check and an engineer-confirmed user outcome visible. A process exiting does not prove Outlook works. A driver installation completing does not prove that a physical page printed.

Record the confirmed targets, execution identity, diagnostic commands and evidence, plan revision, engineer decisions, relevant conditions, step outcomes, recovery attempts and user confirmation. Retain prior attempts when a case is reopened. State local storage's integrity limits honestly; do not describe an administrator-editable journal as independently immutable.

Examples across platforms include investigating Outlook in the context available to SSH on Windows, diagnosing a macOS application failure, and repairing a failed Linux service. Package sources, commands, privilege requirements and verification must fit the actual platform.

## Current baseline and planned expansion

The inspected baseline is v0.9. Its existing support workflow provides fixed investigations, a proposed PowerShell/shell repair script plus verification script, workspace engineer approval, execution records, simulated cases and a dedicated MCP bridge. Windows has Outlook/printing profiles; macOS/Linux have a general profile. The new repair workflow has not been validated through supervised native Outlook or driver repairs.

The agreed design expands that baseline with custom diagnostic execution and target-confirmed investigation sessions, trusted approval adapters inside AI clients, bounded multi-step plans, staged cross-platform rollouts, executable pre-approved recovery, disruption choices, approval windows and durable always-on execution. These are planned requirements, not features to claim as already implemented.

Retain SSH enrollment, client/site authorisation, verified host identities and the existing UI/CLI/MCP surfaces where useful. Inventory and the original fixed-action maintenance interface must not become accidental bypasses around any new policy that is advertised as applying across all interfaces. The accepted limitation of arbitrary diagnostic code must remain explicit.

## Implementation questions to resolve in the plan

The product choices above are agreed. Engineering work still needs to specify the trusted human-approval mechanism available in each AI client, the executable plan/condition format, remote controller authentication and transport, secret storage and privilege handling, and durable scheduling/recovery semantics. In particular, define retries, cancellation, uncertain step outcomes, expected restarts and execution-window overruns before enabling unattended changes.

These details should be worked through in an implementation plan with tests for the enforcement boundaries. This design does not authorise running repairs on real devices, changing installed AI-client settings, removing enrollment expiry, publishing a service or exposing a controller publicly.
