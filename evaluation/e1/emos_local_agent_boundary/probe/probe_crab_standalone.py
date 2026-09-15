"""B10 isolated feasibility probe: drive CrabAgent standalone.

No EMOS Leader, no group discussion, no Habitat environment.  A fixed
robot, a fixed semantic subtask, and a synthetic scene description go
straight into `CrabAgent.init_agent` and `CrabAgent.chat`, answering
whether the EMOS local LLM boundary is callable without the global
organization layer.
"""

from habitat_mas.agents.actions.arm_actions import (
    pick,
    place,
    reset_arm,
)
from habitat_mas.agents.actions.base_actions import (
    nav_to_obj,
    send_request,
    wait,
)
from habitat_mas.agents.crab_agent import CrabAgent

ACTION_POOL = [send_request, nav_to_obj, pick, place, reset_arm, wait]

agent = CrabAgent("agent_0", ACTION_POOL)
agent.init_agent(
    robot_type="SpotRobot",
    task_description="Navigate the robot to the target.",
    subtask_description="Navigate to any_targets|0.",
    chat_history=None,
    enable_logging=True,
    logging_file="probe-agent0-chat-history.json",
)
print("PROBE init_agent OK without Leader; crab self-planning done (subtask->action sequence)")

result = agent.chat(
    "You are just starting the task to take actions. "
    'Here is the current environment description: """\n'
    "The scene contains one target entity any_targets|0 ahead of the robot. "
    "The robot is Spot agent_0.\n"
    '"""\n\nBased on the task, generate the most appropriate next action.'
)
print("PROBE chat result:", result)
print("PROBE token usage:", agent.get_token_usage())
