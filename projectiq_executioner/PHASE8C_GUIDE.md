# Phase 8C — admission assessment and worker qualification foundation

## Scope and release decision

8C.1 implements a fail-closed admission assessor, not a replacement execution
backend. The legacy reviewed workers remain exactly as they were. There is no
new permission flag, automatic approval insertion, alternate executor, model
command execution, or config migration. Every 8C.1 decision is non-authorizing.
Automatic untrusted execution remains **NO GO** on the existing host, even if
all source-free synthetic tests pass. The user can continue the existing
reviewed demo path independently.

The pushed baseline inspected for compatibility is `executioner-generalized`
at `fd688ef0ec506eebf62d95d246af38ada610ce34`. This kit is additive. It uses
8A and finalized 8B contracts, existing pinned checkout, and existing D5d
host/guest diagnostics. It does not edit legacy workers or config. Run the
installer as the trusted operator, not on a student's checkout.

## Admission assessments

```
python -m projectiq_executioner.phase8c assess --repo https://github.com/OWNER/REPO --ref FULL_SHA --out outputs/executioner/fresh_assessment
```

A branch/ref omitted is resolved by the existing bounded pinned checkout. The
assessment recompiles 8A and 8B inside the checkout; never accepts arbitrary
serialized plans as permission. Local `--source-dir` is explicitly unverified.
Optional `--vm-config` checks membership in the existing approved list, current
worker posture and config consistency. It is not an approval authoring route.
Optional `--preparation-dir` points to a trusted local output directory from
8B's completed reviewed preparation. It reads only expected relative names.

Outputs: `execution_plan.json`, `dependency_plan.json`, and
`admission_assessment.json`. Exit 3 means completed assessment with automatic
admission blocked; exit 2 means failed/refused assessment. Output directories
must be new. Source, artifacts and VM config are not modified. This operation
uses zero VM launches and zero package installation or repository execution.
It may acquire the pinned public repository through the existing GitHub client.

Separate preparation and runtime decisions record input/adapter blockers and
worker blockers. Runtime blocker signals are conservative hypotheses from 8A,
not proof of incompatibility. Static/PHP do not need a package job; baked PHP
and MySQL still require runtime validation. A usable npm recipe is not proof
that package lifecycle scripts are unnecessary. Prepared Python wheelhouse
reuse does not prove that transitive dependencies were pinned by the author.
Reviewed candidates still require the original independent source/adapter/
security gate and applicable reviewed assets or test catalogs.

## Artifact consistency

The checker matches the 8B receipt checksum and source/recipe identity, one
job's adapter/components, exact expected artifact/profile filenames, actual
file hashes and sizes, and the exact manifest hashes in the newly compiled
recipe. It checks profile identity, runtime family/version and declared bootstrap
hash. ZIP metadata checks constrain filenames, entry count, declared sizes,
archive family and links. It does not extract/decompress contents, verify nested
wheel internals, install packages, or trust absolute paths embedded in a profile.
The result `MATCHED_REVIEWED_ARTIFACTS_NOT_AUTHORIZED` is a local consistency
check, **not** provenance, a signed receipt, fresh cleanup proof, offline install
verification or runtime permission. A malicious party who rewrites the files
and all hashes can fabricate consistent saved evidence; thus the old artifact
verification and trust boundaries remain essential. Keep the outputs under
trusted operator ownership. Actual bootstrap assets need independent validation
at consumption. Do not reuse evidence from a changed source snapshot.

## Worker inspection and source-free qualification

```
python -m projectiq_executioner.phase8c inspect-worker --vm-config vm_config.json --out outputs/executioner/fresh_worker_inspection
```

Read-only (zero VMs by default). Checks actual host toolchain and pinned image,
current managed runtime root ACL/lease state via existing audit, and the actual
network_spec() and workspace_disk_mib of the two QEMU modes. Records hashes of
selected current worker code/config and the QEMU binary, without printing raw
paths or config. A hash is not a publisher signature. The legacy runtime net
spec is not comprehensive network isolation evidence. The preparation mode
currently uses ordinary user networking without registry enforcement in the
launcher and no bounded workspace attachment. Local ACL PASS is only the
existing reviewed-root check, not proof of all host isolation.

Optional `--run-clean-vms --qemu-sha256 <independently verified operator pin>`
opts in to up to three *existing* source-free D5d checks (process/MySQL,
storage/network, Windows live QEMU posture). Current read-only preflight must
pass and QEMU hash must match the supplied pin. Stops immediately on first
failure or unverified cleanup. Uses existing runners and D5e validation; it
never executes student source. Probe attempts are not confirmed VM launch
counts. A clean legacy qualification is only a fresh baseline observation. It
cannot establish a new worker's security or enable automatic admission.
Do not repeat the optional legacy probes solely to try to make the untrusted
admission gate pass; the structural blockers remain regardless of result.

## What still must be implemented for 8C closeout (8C.2/8C.3)

1. Dedicated execution service on an isolated worker, not the personal
   Windows developer session. No personal credentials, private mounts or
   host-LAN reachability. Separate operator and execution identities. One-time
   deployment-level trust establishment, not per-student approval. Validate
   patched hypervisor, guest image provenance and trust roots independently.
2. Preparation egress containment enforced outside the guest: separate
   fetch-only network boundary with deny-by-default access, registry-specific
   broker or independently enforced outbound policy. DNS, redirects, private
   addresses and arbitrary external connections require validation. No source
   scripts or general network-enabled build. Use a bounded scratch budget for
   preparation as well as runtime. Runtime remains offline.
3. Per-run limits on all guest storage/overlay growth, CPU/memory/processes,
   host worker lifecycle, transfer sizes, and cleanup after cancellation/crash.
   Verify actual controls against trusted source-free synthetic cases; include
   positive controls so blocked probes are meaningful. Bind identity, immutable
   input digest, policy version, and actual worker/image/toolchain identity to
   independently collected evidence. Revalidate immediately at dispatch.
4. Implement and integrate the new worker dispatcher and admission decision
   atomically. Decisions from this package cannot be used as capability tokens;
   a future dispatcher must own its own qualification and checks, not trust
   an edited JSON or config flag. Revoke on changed configuration, missing
   evidence, worker failure, or cleanup uncertainty.
5. Run acceptance on isolated infrastructure: normal unfamiliar compatible
   repos, denied/unsupported inputs, intentional resource exhaustion and
   network-boundary fixtures, and cancellation/cleanup. Keep preparation and
   runtime authorizations separate. Only then remove the *per-source approval
   requirement for the new qualified path*; keep the reviewed path available.
   Phase 8D expands resolvers/adapters and 8E checks end-to-end unseen projects.

There is no guarantee of a perfectly secure hypervisor or execution of all
possible repositories. This release is not a production untrusted-code service.

## Testing

`python -m pytest tests/test_executioner_phase8c.py -q --tb=short`

The new tests use local synthetic source/artifact fixtures, mocked host audit and
probe responses, and real 8A/8B compiler functions. They exercise classification,
receipt and archive validation, zero-permission invariants, source pin matching,
CLI output, and probe stop rules. The full ProjectIQ regression must run on the
actual checkout. No new live VM test or external package acquisition was performed
when packaging this kit. Do not treat the synthetic test count as attestation.
