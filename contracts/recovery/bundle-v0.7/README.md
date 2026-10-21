# Recovery profile v0.7

This explicit profile requires Replica storage v3 and retains v0.6's actual inode/writer,
Memory, full SQL, original content and manifest contracts. Every original Controller spawn,
actual PID identity and original supervisor wait remains in the complete local database.
Missing process journals or supervision markers refuse backup, verification and restore.

Transfer history binds the original source process and independent drain proof. Completed
requires both original drain and the newer target quorum epoch. An uncertain original spawn
remains uncertain after recovery; restore never starts another process, repeats election,
renews authority or replaces the original supervisor's wait with PID absence.

The earlier v0.6 profile retains its v2 requirement. Old tools/binaries refuse v0.7/v3.
See [ADR-0138](../../../docs/decisions/0138-original-controller-child-drain-before-transfer.md).
An infrastructure Controller exit never proves that a robot or Local EAIOS worker stopped.
