# Runtime Reliability Fault Matrix

This matrix maps the first reliability-completion slice to deterministic offline evidence. The tests
inject faults at authority boundaries; they do not claim hardware, network-partition, or distributed
exactly-once certification.

| Fault | Required invariant | Executable evidence |
| --- | --- | --- |
| Controller crashes after intent commit but before Node receipt | Dispatch intent survives; restored nonterminal attempt is `Unknown`; no implicit action replay | `runtime::execution::tests::dispatch_outbox_survives_restore_and_receipt_is_idempotent`, `orchestration::integration_bridge::tests::durable_dispatch_crash_window_restores_as_one_recovery_event` |
| Node crashes around Local EAIOS dispatch | Journal distinguishes pre-dispatch failure from physical ambiguity and never authorizes blind restart | `node_service::journal::tests::ambiguous_dispatch_requires_reconciliation_and_never_restarts`, `node_service::service::tests::reconciliation_required_execution_fences_local_resources_after_restart` |
| Node route or lease is lost | Current attempt becomes `Unknown`; exactly one Runtime recovery transition is emitted; Group recovery stays Control-owned | `runtime::execution::tests::node_loss_emits_one_recovery_event_and_retains_attempt_history`, `integration_server::tests::unavailable_bound_actor_is_deferred_without_failing_server` |
| No replacement exists when recovery begins | Control retains `Blocked + unbound`; later Node evidence can resume the same need through Proposal, Commit, and Rebind | `control::tests::execution_ambiguity_recovery_resumes_after_candidate_registration` |
| Controller restarts after replacement Commit but before Rebind | The timer consumes the existing Control-owned commitment; it never creates a competing Proposal or reservation | `integration_server::tests::recovery_driver_rebinds_existing_commitment_first` |
| Duplicate or stale execution fact | Sequence reducer is idempotent and a wrong owner cannot poison the accepted sequence | `orchestration::integration_bridge::tests::execution_sequence_fences_stale_reconnect_events`, `orchestration::integration_bridge::tests::wrong_node_execution_fact_does_not_poison_sequence` |
| Cancel arrives before Execute | Node stores a tombstone and suppresses the later Local EAIOS invocation | `node_service::journal::tests::cancel_before_execute_is_durable_and_suppresses_dispatch` |
| Cancellation references an unknown execution | HTTP returns conflict, rolls back the batch, and leaves the durable writer usable | `integration_server::tests::unknown_execution_cancel_rolls_back_transaction` |
| Route loss or rejected receipt makes a relation endpoint ambiguous | The relation immediately becomes Unknown and fences progression without waiting for another Node fact | `runtime::relation::tests::physical_ambiguity_refreshes_relation_fences_without_another_fact` |
| Controller restarts while Mission cancellation is draining | `Cancelling` and per-attempt cancel intents survive; ownership is released only by explicit finalization after terminal evidence | `orchestration::tests::cancelling_mission_restores_and_waits_for_explicit_finalization`, `runtime::execution::tests::cancellation_intent_survives_restart_and_terminal_fact_clears_delivery` |
| Recovery rebinds a logical Role | Control requires a committed replacement; Runtime allocates a new attempt while retaining the old attempt and relation logical endpoint | `control::tests::recovery_pipeline_commits_then_rebinds_external_choice`, `runtime::relation::tests::relation_tracks_rebind_without_node_identity`, `runtime::relation::tests::shared_spatial_evidence_follows_rebind_attempt_identity` |
| Node command is accepted but lifecycle fact is delayed | Durable receipt closes the command-delivery ambiguity only; it does not synthesize Running or terminal state | `integration::grpc::tests::v0_4_round_trip_preserves_command_receipt_identity`, `node_service::service::tests::control_bound_command_round_trips_through_generic_engine`, `runtime::execution::tests::dispatch_outbox_survives_restore_and_receipt_is_idempotent` |
| Receipt/evidence carries stale session provenance | The application rejects it before changing Runtime delivery or relation state | `orchestration::integration_bridge::tests::command_receipt_requires_exact_admitted_session`, `integration_server::artifact_http::tests::spatial_map_cross_node_exchange_reaches_strong_verification` |

The cross-module Node Service test exercises formal Protocol v0.4 registration, application
acceptance, durable Execute receipt, generic Local Integration execution, and ordered Runtime facts.
The remaining rows deliberately test lower authority boundaries independently so failures can be
localized without real network or hardware timing.
