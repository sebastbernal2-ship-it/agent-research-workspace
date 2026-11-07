"""Synthetic alignment-memory conformance checks; never an authorization layer."""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from jsonschema import Draft202012Validator, FormatChecker


SCHEMA = json.loads(Path(__file__).with_name("schema.json").read_text(encoding="utf-8"))
VALIDATOR = Draft202012Validator(SCHEMA, format_checker=FormatChecker())


class AlignmentMemoryError(ValueError):
    """Invalid or ambiguous alignment memory input."""


def _time(value: str) -> datetime:
    result = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if result.tzinfo is None:
        raise ValueError("timezone required")
    return result.astimezone(timezone.utc)


def _scope_matches(scope: dict[str, str], *, user_id: str, project_id: str, work_id: str) -> bool:
    return (
        scope == {"type": "user", "scopeId": user_id}
        or scope == {"type": "project", "scopeId": project_id}
        or scope == {"type": "task", "scopeId": work_id}
    )


class ConformanceEngine:
    """Expose which confirmed records affect an action; do not infer authority."""

    def evaluate(
        self,
        *,
        memories: list[dict[str, Any]],
        user_id: str,
        project_id: str,
        work_id: str,
        action: dict[str, str],
        at: str,
    ) -> dict[str, Any]:
        if not isinstance(action, dict) or not action.get("topicId") or not action.get("valueId"):
            raise AlignmentMemoryError("action requires topicId and valueId")
        if not isinstance(memories, list):
            raise AlignmentMemoryError("memories must be a list")
        seen_ids: set[str] = set()
        for memory in memories:
            errors = list(VALIDATOR.iter_errors(memory))
            if errors:
                raise AlignmentMemoryError("invalid memory record: " + errors[0].message)
            if memory["memoryId"] in seen_ids:
                raise AlignmentMemoryError("memory IDs must be unique in a conformance snapshot")
            seen_ids.add(memory["memoryId"])
            if memory["status"] == "confirmed":
                confirmer = memory["source"].get("confirmedBy", {})
                if (
                    confirmer.get("actorType") != "human"
                    or confirmer.get("role") != "user"
                    or confirmer.get("actorId") != user_id
                ):
                    raise AlignmentMemoryError("only this user's explicit confirmation makes a memory binding")

        try:
            instant = _time(at)
        except (TypeError, ValueError, AttributeError) as exc:
            raise AlignmentMemoryError("evaluation timestamp must be timezone-aware") from exc

        by_id = {memory["memoryId"]: memory for memory in memories}
        superseded_ids: set[str] = set()
        for memory in memories:
            superseded_id = memory.get("supersedesMemoryId")
            if superseded_id is None:
                continue
            prior = by_id.get(superseded_id)
            if prior is None:
                raise AlignmentMemoryError("correction must reference a memory in this export snapshot")
            if prior["scope"] != memory["scope"] or prior["topicId"] != memory["topicId"]:
                raise AlignmentMemoryError("correction cannot cross memory scope or topic")
            if memory["status"] == "confirmed" and memory["source"]["sourceType"] != "explicit_correction":
                raise AlignmentMemoryError("a confirmed superseding memory must cite an explicit correction")
            superseded_ids.add(superseded_id)

        relevant: list[dict[str, Any]] = []
        advisory_ids: list[str] = []
        for memory in memories:
            if not _scope_matches(
                memory["scope"], user_id=user_id, project_id=project_id, work_id=work_id,
            ) or memory["topicId"] != action["topicId"]:
                continue
            expiry = memory.get("expiresAt")
            if expiry is not None and _time(expiry) <= instant:
                continue
            if memory["status"] == "observed":
                advisory_ids.append(memory["memoryId"])
            elif memory["status"] == "confirmed" and memory["memoryId"] not in superseded_ids:
                relevant.append(memory)

        hard_rules = [record for record in relevant if record["polarity"] in {"require", "forbid"}]
        required_values = {record["valueId"] for record in hard_rules if record["polarity"] == "require"}
        forbidden_values = {record["valueId"] for record in hard_rules if record["polarity"] == "forbid"}
        ambiguity_ids: set[str] = set()
        if len(required_values) > 1:
            ambiguity_ids.update(record["memoryId"] for record in hard_rules if record["polarity"] == "require")
        if required_values & forbidden_values:
            ambiguity_ids.update(
                record["memoryId"] for record in hard_rules
                if record["valueId"] in required_values & forbidden_values
            )

        action_value = action["valueId"]
        violations = []
        advisories = []
        for memory in relevant:
            mismatch = (
                (memory["polarity"] == "require" and memory["valueId"] != action_value)
                or (memory["polarity"] == "forbid" and memory["valueId"] == action_value)
            )
            soft_mismatch = (
                (memory["polarity"] == "prefer" and memory["valueId"] != action_value)
                or (memory["polarity"] == "avoid" and memory["valueId"] == action_value)
            )
            if mismatch:
                violations.append({"memoryId": memory["memoryId"], "polarity": memory["polarity"], "valueId": memory["valueId"]})
            elif soft_mismatch:
                advisories.append({"memoryId": memory["memoryId"], "polarity": memory["polarity"], "valueId": memory["valueId"]})

        if ambiguity_ids:
            status = "ambiguous"
        elif violations:
            status = "conflict"
        elif advisories:
            status = "review"
        elif relevant:
            status = "consistent"
        else:
            status = "unknown"
        return {
            "alignmentStatus": status,
            "authorizationStatus": "not_checked",
            "needsUserClarification": bool(ambiguity_ids),
            "bindingMemoryIds": sorted(record["memoryId"] for record in relevant),
            "advisoryMemoryIds": sorted(advisory_ids),
            "ambiguityMemoryIds": sorted(ambiguity_ids),
            "violations": violations,
            "advisories": advisories,
        }
