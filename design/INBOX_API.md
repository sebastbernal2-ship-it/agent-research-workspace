# Separate project inbox and VP conversation API contract

Status: product/API design for issue #6. A local project-scoped read model and framework-neutral application facade exist, and a loopback-only HTTP adapter supports the synthetic UI workflow. The service binds a fixed fake principal and one-project allowlist; it is not authentication or production authorization. The route examples below remain conceptual and are not the local adapter's exact paths. Notifications, scheduler continuation, and real identity/membership policy are not implemented.

## Product boundary

- The **project VP conversation** is user-initiated, threaded dialogue. It does not implicitly contain pending approvals.
- The **project inbox** is a separate, queryable list of durable human questions and approvals. A question may link to the VP thread/run that raised it, but resolving it writes a dedicated question event and never fabricates chat text.
- Inbox notification (badge, email, push) is only a reminder. It cannot approve, reject, or resume work.
- Project pages show their own charter, work DAG, discussion, artifacts/evidence, agents/runs, and inbox items. No global memory or another project's record is implicitly mixed into the view.

## Read model

`GET /projects/{projectId}/inbox?status=open&priority=high`

Return only questions authorized for the authenticated user/project. Each list item contains:

- `questionId`, `questionType`, `priority`, `status`, `blocking`;
- `questionText`, `impact`, explicit `options`, `dueAt`;
- `requestedBy`, source `runId`, related `workIds` and current safe work statuses;
- `createdAt`, `updatedAt`, and any supersession/closure metadata;
- opaque `etag`/revision for optimistic concurrency.

Never include model chain-of-thought, credentials, hidden tool payloads, private content excluded by `visibleTo`, or another project's records.

`GET /projects/{projectId}/inbox/{questionId}` returns the same item plus an append-only answer/closure history and the exact dependent work outcome. The client must not infer approval from status absence or from a stale cached list.

## Commands

### Answer

`POST /projects/{projectId}/inbox/{questionId}/answers`

```json
{
  "expectedRevision": 1,
  "idempotencyKey": "client-generated-unique-id",
  "text": "Use current peer-reviewed sources only.",
  "selectedOptionId": "option.current"
}
```

The server derives actor identity from the authenticated session; it never trusts an `actor` field in request JSON. It validates the question is open and the option belongs to it. Answer and exact resume transitions commit atomically. A duplicate identical request returns the original result; changed content under the same idempotency key conflicts. The client cannot choose `resumedWorkIds`.

### Reject/cancel

`POST /projects/{projectId}/inbox/{questionId}/closure`

Only an authenticated user may reject/cancel. A blocking question closure leaves the affected work `blocked`; it does not run the rejected proposal. The reason is required and becomes immutable audit history.

### Supersede

`POST /projects/{projectId}/inbox/{questionId}/supersession`

Creates a new question and marks the old one superseded in one transaction. The successor must bind to the same exact affected work set; work stays paused throughout. The server generates successor identity; the client cannot add extra resume targets.

### Expiry

Expiry is an internal scheduler command, not a user-facing approval action. It requires a due date that has passed, records a system actor and reason, and leaves blocking work blocked. No timeout means approval.

## Concurrency and errors

- Commands require authenticated project membership, current revision/ETag, anti-CSRF protection for browser sessions, request-size/rate limits, and server-generated event IDs.
- Stale revisions return conflict without changing the event log. Unknown project/question IDs return non-disclosing errors where necessary.
- Answer/close/supersede plus work updates use one database transaction. Notifications are emitted after commit through an outbox, with idempotent delivery; notification failure never rolls back or approves the question.
- If a dependency definition in the question's captured closure changed before resolution, reject the stale command and ask the VP/control plane to create a new versioned question rather than resuming an outdated plan.

## UI behavior

- Distinct navigation: `Project → VP chat` and `Project → Inbox`; separate unread/pending badge.
- Approval/permission/budget/tradeoff questions show impact, options, costs/risks, source run, due date, and affected work before explicit action.
- Dangerous or irreversible permissions require a clear scoped confirmation, not a generic “yes” button.
- User can correct a premise or request clarification without selecting a potentially misleading option.
- Display the specific work items that will resume or remain blocked. Provide a durable result page and an audit/history view.
- Keyboard/accessibility support, mobile layout, and visible project identity are required; do not make the inbox a stream hidden inside chat.

## Mapping to current local prototype

The `human_question` record carries type, priority, impact, options, due date, source actor/run and work references. `ProjectControlPlane` tests open/answer/reject/cancel/expire/supersede semantics and atomic affected-work transitions; `InboxReadModel` tests project-scoped filtering, status, history metadata, and ETag change after answer/resume; detail/history use one verified event snapshot to avoid mixed-revision reads. The durable queue integration test opens the pending question and queue in a separate Python process, verifies the work is still `waiting_human` and the job cannot be claimed, then reconstructs the control plane/queue/coordinator, retries an identical user answer without appending another event, and completes exactly one fresh run generation. This is local SQLite/process evidence, not authenticated service recovery. The local command layer uses a verified event-sequence compare-and-swap to reject stale writes atomically; a concurrent change cannot leave half an answer/resume batch. Answer/closure commands compare the required ETag against the same verified question/work snapshot before committing. The application facade obtains actors only from its injected provider and checks the injected project-access policy.

The synthetic UI adapter is in `ui/prototype/local_server.py`; it exposes versioned `/api/v1/health`, `/api/v1/projects/{projectId}`, `/api/v1/projects/{projectId}/inbox`, `/api/v1/projects/{projectId}/inbox/{questionId}`, and answer/closure command routes under loopback only. Unknown API versions return 404 rather than silently falling back. It requires a JSON `expectedEtag`, refuses caller-supplied actor identity, maps stale revisions to HTTP 409, and relies on identical-command replay semantics for safe retries. The adapter applies host/origin/body-size checks but uses a fixed fake principal and single-project allowlist; it is only an offline interaction prototype. Remaining before any broader service: real authenticated principal/session, real membership/authorization policy, production CSRF/session design, rate limiting, scheduler/outbox, notification UX, and independent security/acceptance review.

## Candidate human-client adaptation boundary

An AG2 `HumanClient` or Meldwork Human Gate may deliver a notification, render question context, and collect a human's explicit action, but neither candidate store/channel becomes canonical question or approval state. The trusted UI adapter must submit `questionId`, current ETag, explicit answer/selected option or reject/cancel command to `InboxApplication`; the server derives the authenticated principal and applies membership policy. Only a committed canonical `human.question_answered` or closure event changes question/work state and enables queue continuation. Candidate WAL/gate state is a disposable projection: on restart, reconcile it from canonical question status and never infer approval from a message, timeout, missing gate, or candidate continuation callback. No such transport adapter is implemented or authenticated yet; the offline organization test demonstrates the authority boundary by keeping the human's inbox answer out of AG2/VP discussion messages.
