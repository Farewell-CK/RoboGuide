"""Provider faults and non-recoverable model identity violations."""

from __future__ import annotations

from mission.contract_values import JSONObject


class MissionProviderError(RuntimeError):
    """Report a transport, provider response, or model review failure."""

    def __init__(
        self,
        message: str,
        *,
        status_code: int | None = None,
        transport_failure: bool = False,
        configuration_failure: bool = False,
    ) -> None:
        """Retain structured fault identity without parsing provider diagnostic text."""
        super().__init__(message)
        self.status_code = status_code
        self.transport_failure = transport_failure
        self.configuration_failure = configuration_failure


class MissionIdentityError(MissionProviderError):
    """Retain one identity-invalid draft without authorizing regeneration.

    The response has already been decoded and normalized, so both versions
    can be bound to the frozen request's rejected-draft observations. The
    Request Engine records it once, then preserves the terminal failure.
    """

    stage = "identity_validation"

    def __init__(
        self,
        message: str,
        *,
        provider_output: JSONObject,
        normalized_output: JSONObject,
        generated_at_ms: int,
    ) -> None:
        """Attach the exact candidate and its receipt time to the failure."""
        super().__init__(message)
        self.provider_output = provider_output
        self.normalized_output = normalized_output
        self.generated_at_ms = generated_at_ms
