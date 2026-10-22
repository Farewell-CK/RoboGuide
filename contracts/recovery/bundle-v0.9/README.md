# Recovery profile v0.9

This explicit profile preserves retained Artifact storage v1. It retains v0.8's original Memory
SQL v4/Snapshot protocol, Replica storage v3, full SQL and writer/inode leases. Each Artifact
component must already have selected `ROBOGUIDE_ARTIFACT_STORAGE_SCHEMA=roboguide.artifact-storage/v1`.
Its original owner and old-layout fence are mandatory; the tool never manufactures a replacement.

Artifact managed content is `objects-v1/sha256/<prefix>/<digest>` plus `staging`. Mandatory original
regular files `.retention-owner.json` and `blobs` carry the exact same closed storage identity:
`schema`, OS-random software `owner_id`, and historical migration `source_device/source_inode`.
These are filesystem identifiers, never robot/device facts. The owner is retained across a cold
copy; the original inode is historical and does not assert a restored inode or physical continuity.

All actual original writer leases precede validation and copying. Pending migration, missing owner,
conflicting old-path fence, symlink or malformed metadata refuses publication. Both metadata files
are included and hash-pinned along with all actual CAS bytes. Bundle verification requires them
even when a modified manifest omits their rows and receives a new external pin. Empty managed
directories are preserved. The exact profile selects the layout; verification never queries the
old source root to infer a bundle's format.

`roboguide.restore-receipt/v0.9` preserves original identity/content and creates only fresh empty
process-lock files. Restore starts no services. An explicitly selected current Controller can read
the restored CAS under its existing authorization; legacy configuration refuses the new layout.
Old binaries cannot create the old `blobs` directory because it is a regular-file fence. Old
recovery tools require that old path to be a directory and refuse before bundle publication.

The same declared-cluster Artifact replica sets use `objects-v1` for verified cold content copies;
per-component owner records remain distinct. Such copies do not grant Node placement, Memory-use,
online majority ACK, cross-host recovery or physical continuity. Catalog/lineage/Archive references
and full online content closure remain separately required. See
[ADR-0148](../../../docs/decisions/0148-artifact-owner-identity-and-retained-layout.md).
