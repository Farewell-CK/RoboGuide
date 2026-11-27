# Conversation workbench

Code reviewed: 2026-11-26 · `main@3c7136d6`.

`roboguide chat` joins natural conversation to the existing Mission Request and Controller APIs. The model proposes tool calls; the host validates actual identities, user intent, the displayed plan and the original operation journal before sending effects.

## Start locally

Install the frozen Python environment, then initialize the selected local profile and start the workbench:

```bash
uv sync --dev --frozen
uv run roboguide init
uv run roboguide chat
```

Read-only queries and fixture replay are distinct from planning calls and device execution. Profiles select the actual service/model endpoint and local credentials; do not commit credentials or infer them from an untrusted current-directory file.

## A task’s journey

1. State a goal, or explicitly request planning only. The pending dialogue retains the complete original requirements.
2. Clarification fills parameters. Numbered choices bind only to the question already shown and do not authorize execute, approve or cancel.
3. Inspect the full plan. Execute and risk approval refer to its exact revision/digest and remain separate commands.
4. Observe the original operation while it runs. A timeout or lost reply leaves a pending receipt; do not create another task to recover it.
5. Distinguish Request cancellation from cancellation of an already accepted Mission. Neither a reply nor a Cancel receipt proves physical stopping.

## Diagnose rather than repeat

Ask for current Nodes, registered capabilities, a Mission’s progress or the reason it is still cancelling. The host reads source-bound state and joins exact Mission/Group/attempt stop evidence. Stale, ambiguous and unavailable results remain unknown. Runbook lookup provides bounded excerpts with file/line provenance; documentation commands are data, not permission to act.

The terminal keeps user input editable during work, shows bounded activity, and displays only acknowledged complete answers. F3 exposes turn details; the original raw evidence remains separate from model summaries.

## Implementation and references

[Task tools](../../../mission/src/roboguide_workbench/task_tools.py), [operation journal](../../../mission/src/roboguide_workbench/actions.py), [Mission Request engine](../../../mission/src/mission/request_engine.py), and [task diagnosis](../../../mission/src/roboguide_workbench/mission_diagnosis.py) retain separate responsibilities.

[Versioned workbench profile](reference/conversation-workbench-v0.4.md) · [Planning flow](../../architecture/v2/system-dataflows.en.md) · [Operations](../../operations/README.en.md)
