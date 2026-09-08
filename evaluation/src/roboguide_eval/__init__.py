"""RoboGuide Eval Harness: independent experiment evaluation infrastructure.

The harness plans, runs, and records experiments that compare RoboGuide with
external systems (currently EMOS/Habitat-MAS) without belonging to RoboGuide
Core, Runtime, Control Plane, State & Memory Plane, or any Local EAIOS.
External systems are only ever reached across process boundaries; their
packages are never imported here.
"""

__all__ = ["cli", "config", "metrics", "models", "process", "results", "runner"]
