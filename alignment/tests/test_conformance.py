import unittest

from alignment.conformance import AlignmentMemoryError, ConformanceEngine, prepare_memory_candidate


USER = {"actorType": "human", "actorId": "user.synthetic", "role": "user"}
AGENT = {"actorType": "agent", "actorId": "agent.vp", "role": "vp"}
NOW = "2026-11-07T12:00:00Z"


def memory(
    memory_id,
    *,
    scope_type="user",
    scope_id="user.synthetic",
    topic="autonomy",
    value="external-write",
    polarity="forbid",
    status="confirmed",
    expires_at=None,
    supersedes=None,
):
    source = {
        "sourceType": "user_statement",
        "sourceRef": f"conversation.synthetic.{memory_id}",
        "recordedBy": AGENT,
        "recordedAt": NOW,
    }
    if status == "confirmed":
        source["confirmedBy"] = USER
        source["confirmedAt"] = NOW
    result = {
        "recordType": "alignment_memory",
        "schemaVersion": "0.1",
        "memoryId": memory_id,
        "revision": 1,
        "scope": {"type": scope_type, "scopeId": scope_id},
        "kind": "constraint" if polarity in {"require", "forbid"} else "preference",
        "topicId": topic,
        "valueId": value,
        "polarity": polarity,
        "statement": "Synthetic alignment rule; no user profile data.",
        "status": status,
        "confidence": 1.0 if status == "confirmed" else 0.55,
        "source": source,
        "recordedAt": NOW,
    }
    if expires_at:
        result["expiresAt"] = expires_at
    if supersedes:
        result["source"]["sourceType"] = "explicit_correction"
        result["supersedesMemoryId"] = supersedes
    if status in {"retracted", "superseded"}:
        result["retractionReason"] = "Synthetic correction test."
    return result


class AlignmentConformanceTests(unittest.TestCase):
    def setUp(self):
        self.engine = ConformanceEngine()

    def evaluate(self, memories, *, project="project.alpha", work="work.one", value="external-write"):
        return self.engine.evaluate(
            memories=memories,
            user_id="user.synthetic",
            project_id=project,
            work_id=work,
            action={"topicId": "autonomy", "valueId": value},
            at=NOW,
        )

    def test_confirmed_user_constraint_blocks_and_explains_without_authorizing(self):
        result = self.evaluate([memory("memory.no-external-write")])
        self.assertEqual(result["alignmentStatus"], "conflict")
        self.assertEqual(result["authorizationStatus"], "not_checked")
        self.assertEqual(result["violations"][0]["memoryId"], "memory.no-external-write")
        self.assertEqual(result["bindingMemoryIds"], ["memory.no-external-write"])

    def test_scope_expiry_and_observation_do_not_leak_or_become_binding(self):
        project_only = memory(
            "memory.project-only", scope_type="project", scope_id="project.alpha",
        )
        other_project = self.evaluate([project_only], project="project.beta")
        self.assertEqual(other_project["alignmentStatus"], "unknown")
        self.assertEqual(other_project["bindingMemoryIds"], [])

        expired = memory("memory.expired", expires_at="2026-11-07T11:59:59Z")
        self.assertEqual(self.evaluate([expired])["alignmentStatus"], "unknown")

        observed = memory("memory.inferred", status="observed")
        advisory = self.evaluate([observed])
        self.assertEqual(advisory["alignmentStatus"], "unknown")
        self.assertEqual(advisory["advisoryMemoryIds"], ["memory.inferred"])
        self.assertEqual(advisory["bindingMemoryIds"], [])

    def test_retraction_removes_binding_rule_without_rewriting_history(self):
        old = memory("memory.retracted-old")
        retraction = memory(
            "memory.retraction", status="retracted", supersedes="memory.retracted-old",
        )
        result = self.evaluate([old, retraction])
        self.assertEqual(result["alignmentStatus"], "unknown")
        self.assertEqual(result["bindingMemoryIds"], [])
        self.assertEqual(old["status"], "confirmed")
        self.assertEqual(retraction["status"], "retracted")

    def test_no_opt_in_or_do_not_remember_never_yields_persistable_candidate(self):
        candidate = memory("memory.sensitive-candidate")
        candidate["statement"] = "synthetic private preference marker"
        self.assertIsNone(prepare_memory_candidate(candidate, explicitly_opted_in=False))
        self.assertIsNone(prepare_memory_candidate(
            candidate, explicitly_opted_in=True, do_not_remember=True,
        ))
        prepared = prepare_memory_candidate(candidate, explicitly_opted_in=True)
        self.assertEqual(prepared, candidate)
        self.assertIsNot(prepared, candidate)

    def test_memory_consent_flags_must_be_explicit_booleans(self):
        with self.assertRaisesRegex(AlignmentMemoryError, "explicit booleans"):
            prepare_memory_candidate(memory("memory.consent"), explicitly_opted_in=1)

    def test_explicit_correction_supersedes_old_preference(self):
        old = memory("memory.old", value="external-write", polarity="forbid")
        correction = memory(
            "memory.corrected", value="external-write", polarity="prefer",
            supersedes="memory.old",
        )
        result = self.evaluate([old, correction])
        self.assertEqual(result["alignmentStatus"], "consistent")
        self.assertEqual(result["bindingMemoryIds"], ["memory.corrected"])
        self.assertEqual(result["violations"], [])

    def test_confirmation_from_another_user_is_not_transferable(self):
        foreign = memory("memory.foreign")
        foreign["source"]["confirmedBy"] = {
            "actorType": "human", "actorId": "user.other", "role": "user",
        }
        with self.assertRaisesRegex(AlignmentMemoryError, "this user's"):
            self.evaluate([foreign])

    def test_correction_cannot_supersede_across_scope(self):
        project_memory = memory(
            "memory.project", scope_type="project", scope_id="project.alpha",
        )
        global_correction = memory(
            "memory.global-correction", scope_type="user", scope_id="user.synthetic",
            polarity="prefer", supersedes="memory.project",
        )
        with self.assertRaisesRegex(AlignmentMemoryError, "cannot cross"):
            self.evaluate([project_memory, global_correction])

    def test_conflicting_confirmed_rules_require_user_clarification(self):
        rules = [
            memory("memory.require-a", value="careful-mode", polarity="require"),
            memory("memory.require-b", value="fast-mode", polarity="require"),
        ]
        result = self.evaluate(rules, value="careful-mode")
        self.assertEqual(result["alignmentStatus"], "ambiguous")
        self.assertTrue(result["needsUserClarification"])
        self.assertEqual(result["ambiguityMemoryIds"], ["memory.require-a", "memory.require-b"])

    def test_confirmed_memory_without_user_confirmation_and_unknown_fields_fail_closed(self):
        invalid = memory("memory.unconfirmed")
        invalid["source"].pop("confirmedBy")
        invalid["source"].pop("confirmedAt")
        with self.assertRaises(AlignmentMemoryError):
            self.evaluate([invalid])

        invalid = memory("memory.unknown-field")
        invalid["apiKey"] = "synthetic-not-a-secret"
        with self.assertRaises(AlignmentMemoryError):
            self.evaluate([invalid])


if __name__ == "__main__":
    unittest.main()
