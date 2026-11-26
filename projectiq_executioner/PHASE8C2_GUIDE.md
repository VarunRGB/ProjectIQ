# Phase 8C.2A — Locked npm acquisition and offline cache assembly pilot

## Status and threat model

This is an additive experimental reviewed-only preparation path. It does not modify the existing Phase 8B preparers, Phase 8C.1 admission decisions, approved snapshots, VM template or application runner. **No automatic application or dependency-preparation VM admission is granted.** The new byte downloader does not execute or extract packages on the host. The guest receives generated manifest inputs, not a student checkout. npm itself and registry archives remain attack surfaces. The existing Windows QEMU pilot is not attested for arbitrary untrusted code. Test only an already reviewed pinned fixture with previously verified assets, never an arbitrary unknown repository in the VM.

The new path removes the requirement for networked npm inside the guest. A narrowly scoped host-side downloader retrieves public HTTPS npm tarballs. This is application-level URL/transport enforcement, not a host firewall or separate trusted worker identity. No HTTP proxy, redirect, arbitrary host, arbitrary path, package manager or package execution is allowed by the downloader. DNS results are checked for public IPv4, pinned for the TLS connection, and normal TLS hostname and certificate verification is retained. The host still performs ordinary GitHub snapshot acquisition through the pre-existing bounded ingestion path. Declared SRI ensures equality with submitted lockfile, not trustworthiness of the package. Do not treat SHA pins as signatures.

## New path

1. Resolve exact GitHub commit and regenerate 8A/8B plans. Accept one npm component with no preparation blockers and a lockfile v3. Other components, v2, workspaces, private/nonregistry dependencies, lifecycle hooks on root project, complex references and unsupported version/name patterns fail closed. Construct a reduced package manifest without project scripts and reduced lockfile. Verify exact registry paths against package name/version and declared SHA-512. Exclude incompatible optional platform records for Linux x64/glibc; mandatory mismatch fails. Keep all records in the lockfile for npm's own platform resolution. The planner remains heuristic and does not prove compatibility.
2. With `--fetch`, invoke only the fixed downloader in a separate Python process. HTTP GET only; no redirects, no environment proxy, public IPv4, TLS verification, bounded input, 20,000,000 bytes per package and 140,000,000 bytes aggregate; 1000 lock records max; 145,000,000 bytes total bundle. Per connection timeout and parent 630-second deadline. Fail on missing/mismatched hash. Write the ZIP using opaque tarball bytes, no host tar extraction. The parent attempts cleanup of interrupted download staging. A controller kill may leave inert partial outputs; do not trust or resume them.
3. With `--assemble-reviewed`, independently verify existing approval, pinned Node archive, host toolchain and current managed-root ACL. Use the existing managed run registry, Windows Job Object and restricted QEMU network (`restrict=on`) rather than the old networked dependency-fetch mode. Require at least 24 GiB free on the managed run volume as conservative headroom (not a disk quota). Attach a 1536 MiB workspace; root-controlled bootstrap and cgroup checks must pass. Use a host loopback sentinel with a positive host control, then require the app-UID guest connection attempt to the QEMU host gateway to fail and no sentinel contact. This measures one boundary, not all network channels.
4. Transfer only the generated manifest bundle, pinned Node archive and trusted guest code. Stage the inputs in the bounded workspace. Recheck bundle and tarball digests. Execute `npm ci --offline --ignore-scripts --no-audit --no-fund --no-progress` as the app UID with the existing audited `npm_ci` pre-exec handoff. First use local tarball references; second use the original registry lock in a separate fresh project with the same cache. Both must succeed and both pre-exec handoffs must be observed. Do not run source, project build, server or lifecycle scripts. Transfer only the bounded cache ZIP; verify hash and archive metadata. Publish only after managed stop and cleanup succeed. No automatic retry.
5. Emit `hardening_result.json` with source, recipe checksum, input plan hash, bundle hash, scoped network/assembly observations and artifact receipt. **No 8B profile is generated and no automatic runtime permission follows.** The produced cache is an experimental candidate, not yet used by existing 8B/8C.1 contracts.

## Commands (PowerShell)

```powershell
$run = "outputs\executioner\phase8c2_$(Get-Date -Format 'yyyyMMdd_HHmmssfff')"
$repo = "https://github.com/SafdarJamal/vite-template-react"
$sha = "480dc9128ee90bf7c8c817e52ec063953243b240"
python -m projectiq_executioner.phase8c2 prepare --repo $repo --ref $sha --out "$run\vite_plan"
Get-Content "$run\vite_plan\hardening_result.json" -Raw
```

Expected `DOWNLOAD_PLAN_READY`, zero VMs. If planning passes, and no QEMU processes/managed run uncertainty exist, run one opt-in reviewed test:

```powershell
$node = "$HOME\ProjectIQ_VM\node-v24.22.3-linux-x64.tar.xz"
$nodeSha = "4f2b175a6d53a5c617a80dba2676238a0688cef28c6da4eba35c7d844e66fcfb"
python -m projectiq_executioner.phase8c2 prepare --repo $repo --ref $sha --out "$run\vite_live" --fetch --assemble-reviewed --vm-config .\vm_config.json --node-tarball $node --node-sha256 $nodeSha
Get-Content "$run\vite_live\hardening_result.json" -Raw
```

Use your independently verified previously trusted Node hash, not a self-pin. Expected success `OFFLINE_CACHE_ASSEMBLED_REVIEWED_NOT_AUTHORIZED`, one preparation VM start attempt, zero application VMs, `VM_STOPPED_DISPOSABLE_STORAGE_REMOVED`, two observed pre-exec npm handoffs and scoped host gateway denial. If any stage fails, do not rerun or launch another VM until the receipt and cleanup are inspected. Output may contain large inert tarball/cache archives; share JSON only, never private VM config. Do not commit generated outputs or archives.

## Remaining work

- New pilot has not been tested with a real Windows VM in the authoring environment. Host lifecycle and guest orchestration tests use test doubles; optional actual local npm test uses only a synthetic tarball and no network.
- Python transitive resolution and other managers need independent bounded acquisition. Existing reviewed preparation remains separate and retains network access.
- No full guest-root or host-volume hard quota; bounded workspace, transport size limits and free-space check are not equivalent. Real descendant drain, independent worker identity/host separation, stronger network/firewall enforcement for networked stages, trusted code/asset provenance and end-to-end cancellation/recovery qualification remain outstanding. No change to automatic admission gates.
- Manifest normalization, optional packages and npm implementation behavior require real-lock testing. Native packages needing install scripts may not work. No arbitrary registry URLs or automatic dependency/runtime version inference.
- The scope is one locked npm component; Next.js archives may be planned but runtime support is still absent. Existing reviewed adapter path unchanged.
