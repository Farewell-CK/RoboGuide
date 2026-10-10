"""EMOS Stage2-backed Local How backend for the Habitat bridge.

The adapter injects RoboGuide's committed local assignment at the boundary
where EMOS Stage1 normally supplies ``AgentArguments``. Every decision after
that boundary is made by the original EMOS ``MultiLLMPolicy``,
``LLMHighLevelPolicy``, ``CrabAgent``, ``HierarchicalPolicy``, and configured
skill implementations. RoboGuide does not copy their prompts, retry rules, or
skill dispatcher.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .backend import HabitatBackendConfig, LocalExecutionOutcome
from .emos_stage2 import EmosStage2Runtime, format_stage2_subtask
from .model import (
    RELOCATION_OPERATION,
    SUPPORTED_OPERATIONS,
    CanonicalInvocation,
    CanonicalMobilityInvocation,
    CanonicalRelocationInvocation,
    IntegrationError,
)
from .spatial_feasibility import FloorTransitionProfile

SUBTASK_MODES = ("natural-objective", "entity-grounded")


@dataclass(frozen=True)
class CrabAgentBackendConfig(HabitatBackendConfig):
    """Deployment choices for an original-EMOS Stage2 execution."""

    subtask_mode: str = "natural-objective"
    evidence_dir: Path = Path("crabagent-evidence")
    run_id: str = "unbound"
    spatial_capabilities: tuple[FloorTransitionProfile, ...] = ()
    spatial_profile_path: Path | None = None
    goal_region_navigation: bool = False
    step_aware_navmesh: bool = False
    spatial_navigation_arrival: bool = False
    goal_aware_navigation_arrival: bool = False
    reset_route_support: bool = False
    reset_route_geometry: bool = False
    retain_stopped_session: bool = False
    enable_relocation: bool = False
    relocation_profile_path: Path | None = None
    relocation_completion_binding: bool = False
    endpoint_registry_path: Path | None = None
    enable_observation: bool = False

    def __post_init__(self) -> None:
        """Reject assignment modes that would silently change local semantics."""
        if self.subtask_mode not in SUBTASK_MODES:
            raise IntegrationError(
                f"subtask mode {self.subtask_mode!r} must be one of {SUBTASK_MODES}"
            )
        agent_ids = [profile.agent_id for profile in self.spatial_capabilities]
        if len(agent_ids) != len(set(agent_ids)):
            raise IntegrationError("spatial capability profiles must use distinct agent ids")
        if self.reset_route_support and (
            not self.goal_region_navigation or self.spatial_profile_path is None
        ):
            raise IntegrationError(
                "reset route support requires goal-region navigation and a spatial profile"
            )
        if self.reset_route_geometry and not self.reset_route_support:
            raise IntegrationError("reset route geometry requires reset route support")
        if self.step_aware_navmesh and not self.goal_region_navigation:
            raise IntegrationError("step-aware navmesh requires goal-region navigation")
        if self.spatial_navigation_arrival and not self.step_aware_navmesh:
            raise IntegrationError("spatial navigation arrival requires step-aware navmesh")
        if self.goal_aware_navigation_arrival and not self.spatial_navigation_arrival:
            raise IntegrationError(
                "goal-aware navigation arrival requires spatial navigation arrival"
            )
        if self.relocation_completion_binding and not self.enable_relocation:
            raise IntegrationError("relocation completion binding requires relocation support")
        if self.enable_observation and self.endpoint_registry_path is None:
            raise IntegrationError(
                "read-only observation requires the explicit live endpoint profile"
            )
        if self.endpoint_registry_path is not None and self.retain_stopped_session:
            raise IntegrationError(
                "live endpoint profile does not support cancellation continuation"
            )

    def spatial_capability_for(self, agent_id: int) -> FloorTransitionProfile | None:
        """Return the startup-frozen spatial profile for one Habitat agent."""
        return next(
            (profile for profile in self.spatial_capabilities if profile.agent_id == agent_id),
            None,
        )


class CrabAgentMobilityBackend:
    """Runs mobility through the original EMOS Stage2 policy and skill stack."""

    _runtime_class: type[EmosStage2Runtime] = EmosStage2Runtime

    def __init__(self, config: CrabAgentBackendConfig) -> None:
        """Retain deployment choices without importing EMOS in the HTTP process."""
        if config.agent_id < 0:
            raise IntegrationError("agent_id must be non-negative")
        if config.max_steps < 1:
            raise IntegrationError("max_steps must be positive")
        if config.step_period_ms < 0:
            raise IntegrationError("step_period_ms must be non-negative")
        self._config = config
        self._runtime: EmosStage2Runtime | None = None

    def supported_operations(self) -> tuple[str, ...]:
        """Advertise only operations confirmed by the initialized local runtime."""
        if self._runtime is None:
            return SUPPORTED_OPERATIONS
        advertised = getattr(self._runtime, "supported_operations", None)
        operations = tuple(advertised()) if callable(advertised) else SUPPORTED_OPERATIONS
        if self._config.enable_relocation and RELOCATION_OPERATION not in operations:
            raise IntegrationError("relocation profile is enabled but Stage2 is not ready")
        return operations

    def initialize(self) -> None:
        """Create the persistent simulator and official EMOS policy once."""
        if self._runtime is not None:
            return
        runtime = self._runtime_class(self._config)
        runtime.initialize()
        self._runtime = runtime

    def execute(
        self,
        invocation: CanonicalInvocation,
        cancellation_requested: Any,
        running: Any,
    ) -> LocalExecutionOutcome:
        """Execute one committed semantic assignment through original Stage2."""
        if not isinstance(invocation, (CanonicalMobilityInvocation, CanonicalRelocationInvocation)):
            raise IntegrationError(f"EMOS backend cannot execute {invocation.operation!r}")
        if invocation.operation not in self.supported_operations():
            raise IntegrationError(
                f"EMOS backend does not support {invocation.operation!r} in this deployment"
            )
        if self._runtime is None:
            raise IntegrationError("EMOS Stage2 backend is not initialized")
        return self._runtime.execute(invocation, cancellation_requested, running)

    def readiness_detail(self) -> str:
        """Describe the pinned official policy environment without mutating it."""
        if self._runtime is None:
            return "EMOS Stage2 backend is not initialized"
        return self._runtime.readiness_detail()

    def close(self) -> None:
        """Release the persistent EMOS policy and simulator on their owning process."""
        if self._runtime is not None:
            self._runtime.close()
        self._runtime = None

    def _subtask(self, invocation: CanonicalMobilityInvocation) -> str:
        """Describe the same assignment the original Stage2 runtime will receive."""
        return format_stage2_subtask(invocation, self._config.subtask_mode)
