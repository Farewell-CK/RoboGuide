"""RoboGuide system runner: drives the real RoboGuide path for experiments.

RoboGuide results are only trustworthy when they come from the real
Controller / Node Protocol / Runtime path. This runner must therefore be
wired to a configured command that exercises that path (for example
submitting a Mission through the Controller HTTP API and observing the
resulting evidence); it must never bypass RoboGuide by calling a simulation
skill directly, and it never changes Proposal/Commit/Binding/Runtime
semantics.

Until that wiring lands, the launched command is whatever the local
configuration provides, which keeps this phase fixture-only by construction.
"""

from roboguide_eval.runner import ProcessSystemRunner


class RoboGuideRunner(ProcessSystemRunner):
    """Run the RoboGuide system through its configured real-path command."""

    system = "roboguide"
