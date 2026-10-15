# Memory / Artifact authorization v0.1

Control owns the versioned content registry and data-plane certificate registry. Catalog discovery,
CAS presence, a readable digest and historical Imported evidence independently grant no content use.
The JSON schemas describe bounded input shapes; Rust also checks immutable identities, canonical
manifest digests, tenant attribution, monotonic history and current binding/provider evidence.

The global recovery administrator submits complete registries through:

- `POST /v1/memory-authorizations`
- `POST /v1/artifact-authorizations`

Each body contains `command_id`, `expected_revision` and `registry`. The same authenticated stable
principal and complete original input replay the original receipt; identity conflicts reject.
Corresponding GET routes require global recovery administration. A tenant administrator cannot
replace or inspect the whole deployment registry. Neither route is available to a model output.

Memory policies name an exact selector, canonical complete manifest digest, original tenant,
explicit principal operations and current Group/provider contracts. The manifest digest uses
canonical JSON object-key ordering and SHA-256; it differs from the payload digest. Policies cannot
be removed, reassigned to another tenant/manifest, rewritten at the same revision or unrevoked.
Discovery and content consumption remain distinct. Local scope confines consumption to the original
Node; Group content requires an exact current Task/Role binding, provider and membership generation.
Returning to an earlier Node does not restore an earlier generation. Historical missing membership
remains unavailable. The Controller freezes the content revision and receiving provider in an import
intent, rather than refreshing them during retry or restore.

Artifact mTLS accepts an actual leaf signed by the configured CA, then resolves its **current** Control
grant. `ROBOGUIDE_ARTIFACT_MTLS_ROLE_MAP` accepts the versioned Artifact registry as a trusted startup
bootstrap. Runtime updates use the Controller API; deploy the resulting complete profile before
restart. A stale or missing enrolled profile rejects startup. Stable principal/tenant/Node attribution
survives leaf rotation; actual X.509 expiry and current Node identity lifecycle are separate checks.
Catalog mutations and upload byte writes recheck source authority under the shared writer gate;
blob streams recheck before each bounded chunk. Requests never grant privileges through headers.

Uploads retain Started → Prepared → Completed evidence. Prepared requires this uploader's actual
complete bytes, verified hash/size and fsync. Existing CAS bytes cannot supply that proof. Completion
failure after CAS placement leaves Prepared; exact finalize retry verifies only the original CAS
identity and retains the original admission, deadline and preparation time. `GET
/v1/artifact-uploads/{id}` observes original state. Started does not prove a surviving staging file or
successful transfer; interrupted content is never recreated by observing/retrying upload creation.
Ownership checks use stable principal and tenant, including after leaf rotation. Content publication
needs a completed supplied-byte receipt; scope-changing aliases require a distinct explicit handoff
contract. Current unsupported handoffs reject, including aliases through the typed-map catalog.

Controller wrapper v32, inner checkpoint v21 and upload event codec v23 fence older readers/writers.
Upload ownership history is not eligible for archive deletion before the complete snapshot/replay
contract. Legacy raw loopback/static profiles remain explicit compatibility paths; they do not
provide the complete distributed Memory lifecycle. Node-wide Group grants/revocation acknowledgements,
pre-Execute Memory prerequisites, active-map, complete handoff and referenced-content GC remain
mandatory F4 work. This contract slice is not an overall SoftwareComplete claim.
