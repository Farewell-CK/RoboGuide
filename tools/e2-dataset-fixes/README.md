# COHERENT dataset fixes v1

This tool applies three reviewed semantic corrections before E2 experiments while retaining
Git as the immutable source record and writing a before/after SHA-256 manifest:

- `env3/task0`: mark trash can 25 as an open container (`CONTAINERS`, `OPEN_FOREVER`);
- `env3/task13`: align the quadrotor landing goal with instruction target dining table 3;
- `env4/task15`: make apple 36 manipulable (`GRABABLE`, `MOVABLE`).

The corrections are applied consistently to `PEFA`, `DRMS`, `mcts`, `CRMS`, and
`PEFA_wo_history`. The script fails closed if the reviewed objects or source states differ.
Every future experiment using these files must record dataset variant
`coherent-official+dataset-fixes-v1` and archive the generated manifest.
