"""Offline-only model selection and adapter contract.

This module contains no provider SDKs or network implementation. A real
provider adapter must undergo its own auth, data, budget, and policy review.
"""

from __future__ import annotations

from dataclasses import dataclass
from math import isfinite
from types import MappingProxyType
from typing import Literal, Mapping, Protocol, Sequence

from .agent_router import AGENT_ROLES


EndpointClass = Literal["local", "remote"]
MAX_MODEL_MESSAGES = 256


class ModelRoutingError(RuntimeError):
    """No safe route exists, or a provider violated the normalized contract."""


@dataclass(frozen=True)
class ChatMessage:
    role: Literal["system", "user", "assistant"]
    content: str

    def __post_init__(self) -> None:
        if not isinstance(self.role, str) or self.role not in {"system", "user", "assistant"}:
            raise ValueError("message role must be system, user, or assistant")
        if not isinstance(self.content, str):
            raise ValueError("message content must be text")


@dataclass(frozen=True)
class ModelTask:
    project_id: str
    work_id: str
    run_id: str
    agent_id: str
    role: str
    messages: tuple[ChatMessage, ...]

    def __post_init__(self) -> None:
        for name in ("project_id", "work_id", "run_id", "agent_id"):
            value = getattr(self, name)
            if not isinstance(value, str) or not value.strip() or len(value) > 128:
                raise ValueError(f"{name} must be a non-empty string of at most 128 characters")
        if not isinstance(self.role, str) or self.role not in AGENT_ROLES:
            raise ValueError("role must be a canonical agent role")
        if not isinstance(self.messages, tuple) or not 1 <= len(self.messages) <= MAX_MODEL_MESSAGES:
            raise ValueError(f"messages must be a tuple containing 1 to {MAX_MODEL_MESSAGES} items")
        if any(not isinstance(message, ChatMessage) for message in self.messages):
            raise ValueError("messages must contain only validated ChatMessage records")


@dataclass(frozen=True)
class ModelProfile:
    profile_id: str
    provider_id: str
    model_id: str
    endpoint_class: EndpointClass
    allowed_roles: frozenset[str]
    profile_version: str = "1"
    max_input_chars: int = 100_000
    max_output_tokens: int = 2_000
    max_response_chars: int = 100_000

    def __post_init__(self) -> None:
        for name in ("profile_id", "provider_id", "model_id"):
            value = getattr(self, name)
            if not isinstance(value, str) or not value.strip() or len(value) > 128:
                raise ValueError(f"{name} must be a non-empty string of at most 128 characters")
        if not isinstance(self.endpoint_class, str) or self.endpoint_class not in {"local", "remote"}:
            raise ValueError("endpoint_class must be local or remote")
        if (
            not isinstance(self.allowed_roles, frozenset) or not self.allowed_roles
            or any(not isinstance(role, str) or role not in AGENT_ROLES for role in self.allowed_roles)
        ):
            raise ValueError("profile allowed_roles must be a nonempty frozenset of canonical agent roles")
        if not isinstance(self.profile_version, str) or not self.profile_version.strip() or len(self.profile_version) > 32:
            raise ValueError("profile_version must be a non-empty string of at most 32 characters")
        for name in ("max_input_chars", "max_output_tokens", "max_response_chars"):
            value = getattr(self, name)
            if isinstance(value, bool) or not isinstance(value, int) or value < 1:
                raise ValueError(f"{name} must be a positive integer")


@dataclass(frozen=True)
class ProviderResponse:
    model_id: str
    text: str
    finish_reason: str
    provider_request_id: str | None = None
    input_tokens: int | None = None
    output_tokens: int | None = None
    cost_usd: float | None = None

    def __post_init__(self) -> None:
        if not self.model_id or not isinstance(self.text, str) or not self.finish_reason:
            raise ValueError("provider response requires model identity, text, and finish reason")
        for name in ("input_tokens", "output_tokens"):
            value = getattr(self, name)
            if value is not None and (isinstance(value, bool) or not isinstance(value, int) or value < 0):
                raise ValueError(f"{name} must be a nonnegative integer when reported")
        if self.cost_usd is not None and (
            isinstance(self.cost_usd, bool)
            or not isinstance(self.cost_usd, (int, float))
            or not isfinite(self.cost_usd)
            or self.cost_usd < 0
        ):
            raise ValueError("cost_usd must be finite and nonnegative when reported")


@dataclass(frozen=True)
class RoutedModelResult:
    project_id: str
    work_id: str
    run_id: str
    agent_id: str
    role: str
    routing_revision: int
    profile_id: str
    profile_version: str
    provider_id: str
    model_id: str
    endpoint_class: EndpointClass
    response: ProviderResponse

    def audit_metadata(self) -> dict[str, object]:
        """Return non-prompt, non-credential route and reported-usage metadata."""
        metadata: dict[str, object] = {
            "routingRevision": self.routing_revision,
            "profileId": self.profile_id,
            "profileVersion": self.profile_version,
            "providerId": self.provider_id,
            "modelId": self.model_id,
            "endpointClass": self.endpoint_class,
            "finishReason": self.response.finish_reason,
        }
        if self.response.input_tokens is not None:
            metadata["inputTokens"] = self.response.input_tokens
        if self.response.output_tokens is not None:
            metadata["outputTokens"] = self.response.output_tokens
        if self.response.cost_usd is not None:
            metadata["costUsd"] = self.response.cost_usd
        return metadata


class ModelInvocationPolicy(Protocol):
    """Trusted policy adapter for data destination, approval, and budget gates."""

    def require_invocation(self, task: ModelTask, profile: ModelProfile) -> None: ...


class ModelProvider(Protocol):
    """Provider-specific implementation of normalized text generation."""

    provider_id: str

    def complete(self, profile: ModelProfile, task: ModelTask) -> ProviderResponse: ...


class ModelRouter:
    """Resolve project/role assignments and invoke only the selected adapter.

    There is deliberately no automatic failover. Assignment configuration is
    server-side; the model/task payload cannot nominate a provider or model.
    """

    def __init__(
        self,
        *,
        profiles: Sequence[ModelProfile],
        providers: Sequence[ModelProvider],
        role_defaults: Mapping[str, str],
        project_role_overrides: Mapping[tuple[str, str], str] | None = None,
        routing_revision: int = 1,
        invocation_policy: ModelInvocationPolicy,
    ):
        profile_map = {profile.profile_id: profile for profile in profiles}
        provider_map: dict[str, ModelProvider] = {}
        for provider in providers:
            provider_id = getattr(provider, "provider_id", None)
            if not isinstance(provider_id, str) or not provider_id.strip() or len(provider_id) > 128:
                raise ValueError("provider_id must be a non-empty string of at most 128 characters")
            if not callable(getattr(provider, "complete", None)):
                raise ValueError("provider must expose a callable complete method")
            provider_map[provider_id] = provider
        if len(profile_map) != len(profiles):
            raise ValueError("profile IDs must be unique")
        if len(provider_map) != len(providers):
            raise ValueError("provider IDs must be unique")
        if not profile_map or not provider_map:
            raise ValueError("at least one profile and provider are required")
        for profile in profile_map.values():
            if profile.provider_id not in provider_map:
                raise ValueError(f"profile {profile.profile_id!r} has no registered provider")
        if isinstance(routing_revision, bool) or not isinstance(routing_revision, int) or routing_revision < 1:
            raise ValueError("routing_revision must be a positive integer")
        if not callable(getattr(invocation_policy, "require_invocation", None)):
            raise ValueError("invocation_policy must expose a callable require_invocation method")
        self.routing_revision = routing_revision
        self.invocation_policy = invocation_policy
        self.profiles = MappingProxyType(profile_map)
        self.providers = MappingProxyType(provider_map)
        role_default_map = dict(role_defaults)
        project_override_map = dict(project_role_overrides or {})
        for key in project_override_map:
            if (
                not isinstance(key, tuple) or len(key) != 2
                or not isinstance(key[0], str) or not key[0].strip()
                or not isinstance(key[1], str) or key[1] not in AGENT_ROLES
            ):
                raise ValueError("project overrides require (non-empty project_id, canonical role) keys")
        for role, profile_id in role_default_map.items():
            if not isinstance(role, str) or role not in AGENT_ROLES or not isinstance(profile_id, str):
                raise ValueError("role defaults require canonical roles and string profile IDs")
        for profile_id in project_override_map.values():
            if not isinstance(profile_id, str):
                raise ValueError("project overrides require string profile IDs")
        self.role_defaults = MappingProxyType(role_default_map)
        self.project_role_overrides = MappingProxyType(project_override_map)
        for role, profile_id in self.role_defaults.items():
            self._validate_assignment(role, profile_id)
        for (_project_id, role), profile_id in self.project_role_overrides.items():
            self._validate_assignment(role, profile_id)

    def _validate_assignment(self, role: str, profile_id: str) -> None:
        profile = self.profiles.get(profile_id)
        if profile is None:
            raise ValueError(f"unknown model profile {profile_id!r}")
        if role not in profile.allowed_roles:
            raise ValueError(f"profile {profile_id!r} is not allowed for role {role!r}")

    def resolve(self, project_id: str, role: str) -> ModelProfile:
        if not isinstance(project_id, str) or not project_id.strip() or not isinstance(role, str) or role not in AGENT_ROLES:
            raise ModelRoutingError("project_id and canonical role are required for model routing")
        profile_id = self.project_role_overrides.get((project_id, role))
        if profile_id is None:
            profile_id = self.role_defaults.get(role)
        if profile_id is None:
            raise ModelRoutingError(f"no model profile configured for project/role {project_id!r}/{role!r}")
        profile = self.profiles[profile_id]
        self._validate_assignment(role, profile_id)
        return profile

    def complete(self, task: ModelTask) -> RoutedModelResult:
        if not isinstance(task, ModelTask):
            raise ModelRoutingError("complete requires a validated ModelTask")
        profile = self.resolve(task.project_id, task.role)
        input_chars = sum(len(message.content) for message in task.messages)
        if input_chars > profile.max_input_chars:
            raise ModelRoutingError("request exceeds the selected profile's input limit")
        self.invocation_policy.require_invocation(task, profile)
        provider = self.providers[profile.provider_id]
        response = provider.complete(profile, task)
        if not isinstance(response, ProviderResponse):
            raise ModelRoutingError("provider violated the normalized response contract")
        if response.model_id != profile.model_id:
            raise ModelRoutingError("provider response model identity does not match selected profile")
        if len(response.text) > profile.max_response_chars:
            raise ModelRoutingError("provider response exceeds the selected profile's output limit")
        if response.output_tokens is not None and response.output_tokens > profile.max_output_tokens:
            raise ModelRoutingError("provider usage exceeds the selected profile's output-token limit")
        return RoutedModelResult(
            project_id=task.project_id,
            work_id=task.work_id,
            run_id=task.run_id,
            agent_id=task.agent_id,
            role=task.role,
            routing_revision=self.routing_revision,
            profile_id=profile.profile_id,
            profile_version=profile.profile_version,
            provider_id=profile.provider_id,
            model_id=profile.model_id,
            endpoint_class=profile.endpoint_class,
            response=response,
        )


class DeterministicMockProvider:
    """Offline test adapter; it never accesses a model or network endpoint."""

    def __init__(self, provider_id: str, response_text: str = "mock response"):
        self.provider_id = provider_id
        self.response_text = response_text
        self.calls: list[tuple[str, str]] = []

    def complete(self, profile: ModelProfile, task: ModelTask) -> ProviderResponse:
        self.calls.append((profile.profile_id, task.run_id))
        return ProviderResponse(
            model_id=profile.model_id,
            text=self.response_text,
            finish_reason="stop",
            provider_request_id=f"mock:{task.run_id}",
        )
