"""Explicit canonical schema-version dispatch for event and snapshot records.

This validates versions known to the harness; it does not rewrite old events or
provide state-projection migrations.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from jsonschema import Draft202012Validator, FormatChecker


class SchemaRegistryError(ValueError):
    """Unknown contract version or malformed schema registration."""


class SchemaRegistry:
    def __init__(self, contracts_root: str | Path):
        self.validators: dict[str, Draft202012Validator] = {}
        root = Path(contracts_root)
        schema_paths = sorted(root.glob("v*/schema.json"))
        if not schema_paths:
            raise SchemaRegistryError(f"no versioned canonical schemas found under {root}")
        for schema_path in schema_paths:
            try:
                schema = json.loads(schema_path.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError) as exc:
                raise SchemaRegistryError(f"cannot load canonical schema {schema_path}") from exc
            versions = self._declared_versions(schema)
            version = schema_path.parent.name.removeprefix("v")
            if version not in versions:
                raise SchemaRegistryError(
                    f"schema directory version {version!r} is not declared in {schema_path}"
                )
            if version in self.validators:
                raise SchemaRegistryError(f"duplicate canonical schema version {version!r}")
            Draft202012Validator.check_schema(schema)
            self.validators[version] = Draft202012Validator(schema, format_checker=FormatChecker())

    @staticmethod
    def _declared_versions(schema: Any) -> set[str]:
        versions: set[str] = set()
        if isinstance(schema, dict):
            version_schema = schema.get("schemaVersion")
            if isinstance(version_schema, dict) and isinstance(version_schema.get("const"), str):
                versions.add(version_schema["const"])
            for value in schema.values():
                versions.update(SchemaRegistry._declared_versions(value))
        elif isinstance(schema, list):
            for value in schema:
                versions.update(SchemaRegistry._declared_versions(value))
        return versions

    @property
    def versions(self) -> tuple[str, ...]:
        return tuple(sorted(self.validators))

    def validate(self, record: Any) -> None:
        if not isinstance(record, dict):
            raise SchemaRegistryError("canonical record must be an object")
        version = record.get("schemaVersion")
        validator = self.validators.get(version) if isinstance(version, str) else None
        if validator is None:
            raise SchemaRegistryError(f"unsupported canonical schema version {version!r}")
        errors = sorted(validator.iter_errors(record), key=lambda err: list(err.path))
        if errors:
            detail = "; ".join(f"{list(err.path)}: {err.message}" for err in errors[:5])
            raise SchemaRegistryError(f"canonical record schema validation failed: {detail}")
