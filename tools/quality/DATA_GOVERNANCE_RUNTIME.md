# Data-governance runtime harness

`check_data_governance_runtime.py` owns an independent Linux/WSL workload using explicit
prebuilt Controller and Node binaries. It never connects to field hosts, SSH, a model,
Robonix or a robot. The output directory must be new. The workload creates one private
Controller, actual Node processes, unique mTLS leafs and independent Node state directories.
The shared synthetic HTTP fixture supplies only readonly host observations.

The three modes are:

- `startup`: two real Nodes remain running while the same private Controller/database is
  normally stopped and started thirty times. Initial initialization/migration is recorded
  separately. Each iteration requires a newer Controller-received report from both original
  Nodes, so restored cached inventory alone cannot count as reconnection. Readiness P95 must
  be at most three seconds, measured from actual process creation with mTLS readiness probes.
- `soak`: two real Nodes run for 86,400 seconds by default. The first hour is warmup; a
  five-minute window immediately after warmup is compared with the last five minutes. RSS
  growth must be at most five percent. Controller RSS must remain at most 512 MiB, mean CPU
  at most ten percent of one core, and authenticated inventory/State API P95 at most 200 ms.
  API/freshness errors fail acceptance. A shorter explicit duration is a smoke test, always
  incomplete for the 24-hour gate; no simulated clock is used.
- `stress`: one hundred actual independent software Node processes. The default observation
  duration is five minutes. Every sampled inventory/State read must succeed and contain all
  hundred fresh Nodes. Its CPU/RSS are recorded separately; it does not claim two-Node or
  24-hour acceptance.

Workload defaults are fixed and recorded: one-second Controller heartbeats, five-second
leases, two-second reported State exports with ten-second TTL, and 4096 repeated payload
bytes per Node. A real Node acquisition/export supplies each source identity; the fixture
does not fabricate Controller inventory or claim physical measurements. No Task is submitted.
This fresh private SQL workload does not prove preservation of the original 48 field
Missions or one-million-row historical recovery; use the separate snapshot recovery gates.
Retention remains the candidate Controller's default evaluation policy; this harness does
not silently enable field or private deletion. Thirty-one-day retention tests remain separate.

The owner, source fixture and every child must inherit one actual cgroup v2 scope with
finite `memory.max` at most **4 GiB**, `memory.swap.max=0`, and CPU quota at most **two
cores**. A bare invocation without those whole-run limits is refused. Child affinity and
2 GiB address-space limits are additional protections; they do not substitute for the
shared scope. Changed scope limits or an OOM kill fail acceptance. The fixture and metric
observer are inside the same scope. Credential validity is three days, covering a real
24-hour run with setup margin. Selected source, binary and Node configuration digests,
kernel boot identity, host details, sampling settings and original PID/start ticks are
written before measurements. `/proc/<pid>/stat` supplies actual Controller CPU and RSS;
`/proc/<pid>/status` VmHWM also enforces the peak RSS bound between sampling points.
Every guard retains whole-cgroup memory current/peak, memory events and CPU throttling
counters, covering the fixture/owner and all Node processes rather than only the Controller.
`metrics.jsonl` is flushed each sample and fsynced periodically; `progress.json` is replaced
atomically. `owned-processes.json` identifies exact process groups. SIGTERM/SIGINT clean up
only those owned groups after verifying their Linux start identity, preserving SQL and logs.

Disk budget defaults require at least **2 GiB** free before setup, retain **512 MiB** as
an emergency free-space reserve, and cap all private output at **2 GiB** including SQL,
WALs, Node journals, archives, certificates and process logs. Reaching a bound stops the
workload and preserves collected evidence. The harness does not clean unrelated build
caches or field data. One hundred default 4 KiB State values fit the bounded 2 MiB API
response; oversized explicit workload combinations are rejected before launch. The
source listener uses a 256-connection backlog for real hundred-Node sampling bursts.

When sharing ThinkPad with the field deployment, the parent operator must first back up
and stop the production Controller and dependent MI within the authorized maintenance
window. Pass `--maintenance-controller-unit rg-controller.service`; the harness only reads
the unit state and refuses to start or continue if that service becomes active. The
harness does not stop/restart production units itself. Use no simultaneous production
Controller or second field comparison process. Generated private listeners use unrelated
loopback ports and private SQL, not the original robot endpoints or data directories.

Example commands, to run only after the final binaries are frozen:

```bash
systemd-run --user --scope --unit=rg-benchmark-startup \
  -p MemoryMax=3G -p MemorySwapMax=0 -p CPUQuota=200% -p TasksMax=768 \
  python tools/quality/check_data_governance_runtime.py startup \
  --controller /private/frozen/integration-server \
  --node /private/frozen/roboguide-node --output /private/new-startup-30 \
  --maintenance-controller-unit rg-controller.service

systemd-run --user --scope --unit=rg-benchmark-soak \
  -p MemoryMax=3G -p MemorySwapMax=0 -p CPUQuota=200% -p TasksMax=768 \
  python tools/quality/check_data_governance_runtime.py soak \
  --controller /private/frozen/integration-server \
  --node /private/frozen/roboguide-node --output /private/new-soak-24h \
  --maintenance-controller-unit rg-controller.service

systemd-run --user --scope --unit=rg-benchmark-stress \
  -p MemoryMax=3G -p MemorySwapMax=0 -p CPUQuota=200% -p TasksMax=768 \
  python tools/quality/check_data_governance_runtime.py stress \
  --controller /private/frozen/integration-server \
  --node /private/frozen/roboguide-node --output /private/new-stress-100 \
  --maintenance-controller-unit rg-controller.service
```

The program exits 2 when a completed measurement fails or cannot establish its acceptance
gate. It leaves raw metrics and the exact result, rather than lowering thresholds. A short
soak intentionally exits 2 because it has not established 24-hour stability. Errors and
interruptions leave `run.json` failed and preserve owned-process cleanup evidence.

## First-migration snapshot budget

The separate `check_catalog_checkpoint_processes.py` copies one retained database snapshot,
then compares legacy streaming, first migration and ordinary restart. Each probe keeps its
2 GiB address-space bound. The default CPU/wall budgets remain 180/240 seconds. A genuinely
large first migration can explicitly use a higher finite budget; only that migration receives
the override, while streaming and restart retain their original limits. Both new options
accept integer seconds from 1 through 3600. Invalid options are rejected before creating
the output directory. This changes experiment resource allocation, not Controller readiness,
RSS, history-preservation or runtime acceptance thresholds.

For example, use the existing probe executable and a verified private copy of the large
field snapshot, in a new evidence directory:

```bash
python tools/quality/check_catalog_checkpoint_processes.py \
  --probe /private/frozen/catalog_recovery_probe \
  --snapshot /private/retained-field-snapshot.sqlite3 \
  --storage-schema 3 \
  --migration-cpu-seconds 900 --migration-wall-seconds 1200 \
  --output /private/new-catalog-v3-migration-900cpu-1200wall
```

Add `--previous-controller /private/frozen/previous-integration-server` when the genuine
prior-writer refusal check is available. Each probe writes its exact configured limits
to `<phase>.limits.json` before launch, preserving the budget even on timeout or failure.
Successful phase measurements include `resource_limits`; `result.json` repeats all phase
limits in `probe_resource_limits`. The first-migration budget never applies to the old-writer
refusal probe. No live Controller, Node, robot or model is launched by this snapshot tool.

ThinkPad audit before first runtime: use a complete frozen source tree, because this
script resolves the committed host Node fragment relative to its repository root. Its
Node config is v0.14 and requires the selected Node binary to support that generation;
the initial smoke must verify actual process logs/readiness rather than assuming a cached
Node executable is compatible. The three ports are private dynamically selected loopback
listeners. Certificate mapping is complete and every Node gets its own leaf/journal; the
global recovery-admin observer reads exact source/channel/epoch/sequence and Controller
timestamp/TTL for all Nodes. Each restart keeps the original Node processes alive and
requires a report newer than the baseline taken after the restarted Controller is ready.

The ThinkPad's reported 4.4 GiB free space is not an allocation guarantee: retain room for
the production backup and candidate program before checking the actual new output volume.
Do not reduce the disk reserve or run against the production SQL to get a measurement.
If `MemoryMax`, `MemorySwapMax` or `CPUQuota` is unavailable in the actual user scope,
the harness refuses to start. Do not replace the aggregate cgroup with one independent
limit per Node. An out-of-memory kill, changed scope limits, live production service,
disk reserve breach or source/binary digest change invalidates the run and leaves evidence.

2026-11-10 implementation status: Windows Ruff, format, function-documentation checks and
strict mypy using `--platform linux` passed; all **40** pure acceptance/guard tests passed.
These checks include the exact five-percent boundary, missing observations, PID reuse,
single-core CPU accounting and refusal to count short runs as 24-hour evidence. **Linux process
execution is pending**: local WSL reported startup `E_UNEXPECTED` after filesystem
I/O/read-only failures. The user subsequently selected ThinkPad as the authorized host;
only the parent manages its maintenance and staging. No workload from this harness has
been launched, and production has not been stopped by this tool. These
static tests are not runtime acceptance, not 24-hour evidence and not ThinkPad deployment
approval. Existing successful unrelated acceptance logs are preserved.
