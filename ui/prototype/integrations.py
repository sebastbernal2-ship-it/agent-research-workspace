"""Per-project integrations: external tools (MCP servers) a project's workers may call.

A project declares what its workers can reach (a research database, a literature service, a deploy tool) without any
of it being baked into Northstar. The same declaration is handed to whichever harness runs the worker: Claude Code via
``--mcp-config``, Codex via ``-c mcp_servers.<name>...``. Secrets are never stored: ``secretEnv`` maps a variable to the
NAME of an environment variable that holds it. Declaring a server is the owner's approval for its tools to be called
without a prompt, so declare only servers you trust.
"""

from __future__ import annotations

import json
import os
import re
import threading
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

NAME = re.compile(r"[a-z][a-z0-9_]{0,31}")
ENV_NAME = re.compile(r"[A-Z_][A-Z0-9_]{0,63}")
SECRETISH = re.compile(r"(KEY|TOKEN|SECRET|PASSWORD|CREDENTIAL|AUTH)", re.I)
MAX_ARGS, MAX_INTEGRATIONS = 16, 12


def validate(name: Any, spec: Any) -> dict[str, Any]:
    """Return the normalized spec or raise ValueError saying exactly what is wrong."""
    if not isinstance(name, str) or not NAME.fullmatch(name):
        raise ValueError("integration name must be lowercase letters, digits or underscores, starting with a letter")
    if not isinstance(spec, dict) or set(spec) - {"kind", "command", "cwd", "url", "env", "secretEnv", "description"}:
        raise ValueError("integration spec accepts kind, command, cwd, url, env, secretEnv and description")
    kind = spec.get("kind")
    out: dict[str, Any] = {"kind": kind, "description": str(spec.get("description") or "")[:300]}
    if kind == "mcp-stdio":
        command = spec.get("command")
        if (not isinstance(command, list) or not 1 <= len(command) <= MAX_ARGS
                or any(not isinstance(a, str) or not a for a in command)):
            raise ValueError("mcp-stdio needs command as a non-empty argv list of strings (not a shell string)")
        out["command"] = command
        if spec.get("cwd") is not None:
            if not isinstance(spec["cwd"], str) or not os.path.isabs(spec["cwd"]):
                raise ValueError("cwd must be an absolute path")
            out["cwd"] = spec["cwd"]
    elif kind == "mcp-http":
        url = spec.get("url")
        parts = urlsplit(url) if isinstance(url, str) else None
        local = bool(parts) and parts.hostname in {"127.0.0.1", "localhost"}
        if not parts or not (parts.scheme == "https" or (parts.scheme == "http" and local)) or not parts.hostname:
            raise ValueError("mcp-http needs an https URL (http only for localhost)")
        out["url"] = url
    else:
        raise ValueError("kind must be mcp-stdio or mcp-http")
    env = spec.get("env") or {}
    if not isinstance(env, dict) or any(not isinstance(k, str) or not ENV_NAME.fullmatch(k) or SECRETISH.search(k)
                                         or not isinstance(v, str) or len(v) > 500 for k, v in env.items()):
        raise ValueError("env holds non-secret NAME: value strings only; put secrets in secretEnv")
    secret = spec.get("secretEnv") or {}
    if not isinstance(secret, dict) or any(not isinstance(k, str) or not ENV_NAME.fullmatch(k)
                                           or not isinstance(v, str) or not ENV_NAME.fullmatch(v) for k, v in secret.items()):
        raise ValueError("secretEnv maps a variable to the NAME of an environment variable that holds the secret")
    out["env"], out["secretEnv"] = env, secret
    return out


def resolve_env(spec: dict[str, Any], environ: dict[str, str]) -> tuple[dict[str, str], list[str]]:
    """The server's environment, plus the secret variables that are not set (so the integration can be skipped honestly)."""
    env, missing = dict(spec.get("env") or {}), []
    for var, source in (spec.get("secretEnv") or {}).items():
        if environ.get(source):
            env[var] = environ[source]
        else:
            missing.append(source)
    return env, missing


def usable(specs: dict[str, dict[str, Any]], environ: dict[str, str]) -> tuple[dict[str, dict[str, Any]], dict[str, list[str]]]:
    """Split the declared integrations into those that can start now and those missing a secret."""
    ok, blocked = {}, {}
    for name, spec in specs.items():
        _env, missing = resolve_env(spec, environ)
        if missing:
            blocked[name] = missing
        else:
            ok[name] = spec
    return ok, blocked


def claude_config(specs: dict[str, dict[str, Any]], environ: dict[str, str]) -> dict[str, Any]:
    """Claude Code's --mcp-config document."""
    servers: dict[str, Any] = {}
    for name, spec in specs.items():
        env, _ = resolve_env(spec, environ)
        if spec["kind"] == "mcp-http":
            servers[name] = {"type": "http", "url": spec["url"]}
        else:
            servers[name] = {"command": spec["command"][0], "args": spec["command"][1:], **({"env": env} if env else {})}
    return {"mcpServers": servers}


def claude_tools(specs: dict[str, dict[str, Any]]) -> list[str]:
    return [f"mcp__{name}" for name in specs]  # permits every tool of a server the owner declared


def codex_args(specs: dict[str, dict[str, Any]], environ: dict[str, str]) -> list[str]:
    """Codex's repeated ``-c mcp_servers.<name>...`` overrides. Values are JSON, which is valid TOML for these types."""
    args: list[str] = []
    for name, spec in specs.items():
        key = f"mcp_servers.{name}"
        if spec["kind"] == "mcp-http":
            args += ["-c", f"{key}.url={json.dumps(spec['url'])}"]
        else:
            args += ["-c", f"{key}.command={json.dumps(spec['command'][0])}", "-c", f"{key}.args={json.dumps(spec['command'][1:])}"]
            if spec.get("cwd"):
                args += ["-c", f"{key}.cwd={json.dumps(spec['cwd'])}"]
            env, _ = resolve_env(spec, environ)
            if env:
                args += ["-c", f"{key}.env={{ " + ", ".join(f'{json.dumps(k)} = {json.dumps(v)}' for k, v in env.items()) + " }"]
        args += ["-c", f'{key}.default_tools_approval_mode="approve"']
    return args


class IntegrationStore:
    """Declarations per project: a local setting (like automation), not canonical project history."""

    def __init__(self, path: Path):
        self.path, self._lock = path, threading.Lock()

    def _read(self) -> dict[str, dict[str, dict[str, Any]]]:
        try:
            data = json.loads(self.path.read_text(encoding="utf-8"))
            return data if isinstance(data, dict) else {}
        except (OSError, ValueError):
            return {}

    def get(self, project_id: str) -> dict[str, dict[str, Any]]:
        value = self._read().get(project_id)
        return value if isinstance(value, dict) else {}

    def _write(self, data: dict[str, Any]) -> None:
        tmp = self.path.with_suffix(".tmp")
        tmp.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
        os.chmod(tmp, 0o600)
        tmp.replace(self.path)

    def set(self, project_id: str, name: str, spec: Any) -> dict[str, Any]:
        clean = validate(name, spec)
        with self._lock:
            data = self._read()
            mine = data.setdefault(project_id, {})
            if name not in mine and len(mine) >= MAX_INTEGRATIONS:
                raise ValueError(f"a project can declare at most {MAX_INTEGRATIONS} integrations")
            mine[name] = clean
            self._write(data)
        return clean

    def remove(self, project_id: str, name: str) -> bool:
        with self._lock:
            data = self._read()
            found = name in data.get(project_id, {})
            data.get(project_id, {}).pop(name, None)
            self._write(data)
        return found
