# Separate project inbox and VP conversation API contract (design only)

Status: proposed interface for issue #6. No HTTP server, UI, notifications, authentication, or scheduler continuation is implemented. All paths below are conceptual; the local Python control plane is not an API service.

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

The `human_question` record carries type, priority, impact, options, due date, source actor/run and work references. `ProjectControlPlane` tests open/answer/reject/cancel/expire/supersede semantics and atomic affected-work transitions. Remaining before this design can become an API: authenticated principal/session, versioned work/closure snapshots, scheduler/outbox, notification UX, cross-project authorization and a real independent acceptance review.
