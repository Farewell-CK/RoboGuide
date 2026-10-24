# Original private staging recovery v0.22

This explicit profile retains v0.21 and admits Root8, declared as
`roboguide.replica-content-layout/v0.8` with mandatory nullable `restore_binding_digest`.
Both `content_staging_gc_commands` and `content_staging_gc_items` and their exact
`staging_gc_ids` / `staging_gc_item_ids` inventories are mandatory even when empty.

Each cleanup freezes up to 64 original copy/stream IDs, complete source/release digests,
provider-derived private leaves and the actual file identity/length or absence at admission.
All initial Unknown items are committed before payload effects. Only original copy completion
or independently observed original process end plus released ownership, or an exact retained
stream retirement, admits cleanup. Outcomes retain their actual partial-byte digest and original
nonrenewing budget; no partial digest becomes full CAS or use evidence.

Verification independently checks every intent/item/source/retirement and exact inventory.
Restore appends backing binding v0.4 with source Root8, retaining all prior raw bindings and
original outcomes/budgets. Private staging payloads remain excluded from bundle contents.
Restored absence never fills an old Unknown. Original active references still require complete
qualified CAS bytes. Restore starts no services and creates no Node/device/physical facts.
Older profiles refuse Root8, including when both new inventories are empty.
