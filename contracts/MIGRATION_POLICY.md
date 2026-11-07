# Canonical record and schema migration policy

Status: design decision for draft v0.1; no migration has been implemented yet.

## Compatibility rules

1. A stored event, immutable record snapshot, and content-addressed blob are historical facts. Never rewrite them to a newer schema in place.
2. `schemaVersion` identifies the record's wire contract. Unknown versions fail closed; a reader must not guess or silently drop fields.
3. New writes use the current supported version. A future incompatible contract gets a new schema version and an explicit migration function; changing the meaning of a v0.1 field requires a new version.
4. Migrations are deterministic, pure, version-to-version transforms. They produce a derived projection/input; the original serialized record and event export remain byte-for-byte available.
5. Apply at most one version step at a time, validate both the source and result against their respective schemas, and test migration idempotence (same source always produces the same result). Never provide a lossy downgrade path.
6. Keep old-version readers/migrations available for all retained data. If a migration cannot preserve meaning, quarantine the record and stop that projection rather than fabricate defaults.
7. Event replay order and idempotency are defined by the original canonical event envelope, not by a migrated event copy. A migration must not create new authoritative events as a side effect.
8. Schema changes, migration code, and fixture migrations land together with compatibility tests and an export/replay check before any writer emits the new version.

## v0.1 disposition

- v0.1 is a draft and has no deployed user data. Its schema is closed (`additionalProperties: false`) and unknown sensitive fields are rejected.
- The local SQLite prototype currently validates v0.1 only. It does not yet implement migration dispatch, compatibility readers, retention, or quarantine UX.
- Before any production or private-data use, introduce a version-dispatch validator and a second-version migration test; do not mutate the current v0.1 schema to smuggle in changed semantics.

## Required test matrix for the first migration

- Old v0.1 fixture remains valid and byte-identical after export.
- Forward migration yields a valid new-version record and is deterministic/idempotent.
- Unknown version, missing required source fields, unknown sensitive fields, and impossible enum transitions fail closed.
- Event replay before/after projection migration yields equivalent project/work/question meaning.
- Failure in a migration does not append events or partially replace the source snapshot.
