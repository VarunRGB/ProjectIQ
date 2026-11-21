# ProjectIQ — One-Call Executioner Agent (reviewed-repository orchestration)

**Scope:** deterministic Python workflow. Reuses the existing Phase 2D planner,
existing SHA-pinned Phase 2C VM executor, and existing trusted dependency-cache
preparation helpers. No production VM isolation or allowlist checks are changed.

**Important:** This is a *single-call interface*, **not** automatic security
approval of arbitrary untrusted submissions. The exact `owner/repo@40-char-SHA`
**must already be listed** in the operator's `vm_config.json` to execute.
Unknown commits receive a plan and `NEEDS_OPERATOR_APPROVAL` instead of running.
The D5e `NO_GO` decision for general untrusted student projects remains in force.

## Architecture

```text
main ProjectIQ orchestrator / PowerShell CLI
         |
         v
ExecutionerAgent.run(repo_url, output_dir, ref=None)
         |
         +-- (1) Phase 2D run_pipeline [plan-only]
         |       Resolve GitHub ref -> immutable 40-char SHA;
         |       download bounded source ZIP; analyze architecture;
         |       generate Phase 2A / 2B / 2C / 2D plans
         |
         +-- (2) Adapter selection (existing contracts)
         |       Static HTML, PHP/MySQL, Vite, Express, Flask/FastAPI,
         |       Vite+Express full-stack
         |
         +-- (3) Exact SHA operator approval (vm_config.json)
         |       Missing SHA -> NEEDS_OPERATOR_APPROVAL; stop
         |
         +-- (4) Verify pinned offline dependency profile
         |       Static/PHP: none needed
         |       Node/Python: verify existing profile hashes
         |       Optional: trusted networked cache-prep VM only with
         |       explicit network authorization + pinned runtime assets
         |
         +-- (5) execute_vm_candidate [existing reviewed-fixture runner]
         |       Re-download immutable SHA; deny guest egress;
         |       start bounded disposable QEMU VM
         |
         +-- (6) Verify source, adapter, backend, guest HTTP 200,
                 security attestation and disposable VM cleanup
         |
         v
outputs/executioner/<run>/orchestration_result.json
```

Steps are all invoked by **one Python function call or one PowerShell CLI call**.
The underlying tools still perform separate checks; this agent does *not*
replace them with shell commands or an LLM that guesses what to run.

## Apply on `executioner`

This Git patch adds only:

- `projectiq_executioner/phase2/phase2d/executioner_agent.py`
- `projectiq_executioner/tests/test_phase2d_executioner_agent.py`
- `projectiq_executioner/PHASE2D_ONE_CALL_EXECUTIONER_AGENT_GUIDE.md`

From the repository root:

```powershell
$patch = "$env:USERPROFILE\Downloads\projectiq_onecall_executioner_agent.patch"
git apply --check --verbose $patch
if ($LASTEXITCODE -ne 0) { throw 'Agent patch check failed' }
git apply $patch
if ($LASTEXITCODE -ne 0) { throw 'Agent patch failed' }
git diff --check
if ($LASTEXITCODE -ne 0) { throw 'Diff validation failed' }

python -m unittest projectiq_executioner.tests.test_phase2d_executioner_agent -q
if ($LASTEXITCODE -ne 0) { throw 'Agent offline tests failed' }
```

## ONE CALL — previously approved teacher repository

The teacher's exact commit SHA must have been reviewed and entered under
`approved_snapshots` in `vm_config.json`. This is a one-time **operator decision**,
not an automatically generated approval. For existing Node/Python profiles,
their valid offline asset entries must also be in `vm_config.json`.

```powershell
$repo = 'https://github.com/Teacher/StudentPortal'
$out = 'outputs/executioner/teacher_demo_run_001' # use a fresh directory

python -m projectiq_executioner.phase2.phase2d.executioner_agent `
    --repo $repo `
    --vm-config vm_config.json `
    --out $out

$rc = $LASTEXITCODE
$r = Get-Content "$out/orchestration_result.json" -Raw | ConvertFrom-Json
$r | Select-Object status, adapter, execution, http_status, cleanup, failure_code
$r.steps | Format-Table name, status
$r.next_action | Format-List *
if ($rc -ne 0) { Write-Warning 'Inspect structured result; do not claim execution succeeded' }
```

The agent resolves HEAD to a full SHA and compares it to the existing
allowlist. If HEAD changed after approval, execution stops until you review
and approve the new SHA. `--ref <full-sha>` can pin an older reviewed commit.

**Successful execution** requires `STARTUP_VERIFIED`, HTTP 200, the expected
reviewed VM provider and `VM_STOPPED_DISPOSABLE_STORAGE_REMOVED`.

## ONE CALL — unknown teacher repo (planning safely)

The same CLI call produces a plan and reports `NEEDS_OPERATOR_APPROVAL` if the
stack is supported but its exact SHA is not allowlisted. It does **not** make
policy changes or launch a VM. Review `plan/phase2c_candidate.json`, the
source code and the `review_identity` in `orchestration_result.json`. After
review, add that exact SHA to `approved_snapshots` in your operator-controlled
`vm_config.json`, then run again using a new `--out` directory.

Unsupported stack -> `NEEDS_ADAPTER`, with no execution. Use `--plan-only`
(without `--vm-config`) to generate a plan without requesting execution.

## ONE CALL — auto-prepare Node/Python offline dependencies

Only for a **previously approved exact SHA**. The normal execution VM stays
offline. Preparation uses the preexisting separate networked fetch VM. The
operator must explicitly opt in to trusted dependency fetching, and provide
pinned Linux runtime files and SHA-256 hashes. No URL or command from the
teacher's repository is promoted into a host shell command.

Node Vite / Express / Vite+Express (using an already downloaded official
Linux Node v24 archive):

```powershell
python -m projectiq_executioner.phase2.phase2d.executioner_agent `
    --repo $repo --vm-config vm_config.json `
    --out outputs/executioner/teacher_node_demo_001 `
    --auto-prepare --allow-trusted-dependency-network `
    --node-tarball 'C:\path\node-v24.22.3-linux-x64.tar.xz' `
    --node-sha256 '<64-lowercase-or-uppercase-hex-digits>'
```

Flask/FastAPI:

```powershell
python -m projectiq_executioner.phase2.phase2d.executioner_agent `
    --repo $repo --vm-config vm_config.json `
    --out outputs/executioner/teacher_python_demo_001 `
    --auto-prepare --allow-trusted-dependency-network `
    --pip-wheel 'C:\path\pip-25.2-py3-none-any.whl' `
    --pip-sha256 '<64-lowercase-or-uppercase-hex-digits>'
```

If the offline profile is already verified, preparation is skipped and the
network opt-in is not needed. The agent stores newly prepared cache/profile
artifacts under `<out>/prepared_assets` and **uses the new profile only in
memory** for this run: it does *not* mutate persistent `vm_config.json`. If you
want to reuse the profile in later demo runs, review it and add the exact
profile mapping to your operator config yourself.

Do **not** use trusted dependency preparation on unknown or potentially
malicious submissions without an explicit review of source, dependencies and
supply-chain risk. `npm --ignore-scripts` and binary-only Python wheels reduce
risk but are not universal guarantees.

## Python orchestrator API

```python
from pathlib import Path
from projectiq_executioner.phase2.phase2d.executioner_agent import (
    ExecutionerAgent, DependencyPreparation,
)

agent = ExecutionerAgent(vm_config=Path('vm_config.json'))
result = agent.run(
    'https://github.com/Teacher/StudentPortal',
    output_dir=Path('outputs/executioner/teacher_demo_run_001'),
)

if result['status'] == 'STARTUP_VERIFIED':
    # Attach result['artifacts'] and the verified VM report to your UI.
    pass
elif result['status'] == 'NEEDS_OPERATOR_APPROVAL':
    # Present result['next_action']; no student code executed.
    pass
else:
    # Display status/failure_code; do not report false success.
    pass
```

For on-demand *already-approved* cache preparation, initialize
`DependencyPreparation(enabled=True, allow_trusted_network=True,
node_tarball=..., node_sha256=...)` or provide pinned pip wheel fields and pass
it into `ExecutionerAgent(...)`. This is an explicit operator-side choice.

The structured result provides `status`, `source`, `adapter`, `steps`,
`failure_code`, `next_action`, `execution`, `cleanup`, `http_status`,
`security_attestation` and paths relative to the chosen output directory.
Unique output directories are required to preserve evidence; no previous run
report is overwritten.

## Validation before a demo

- Run the 31 focused offline tests on Windows.
- Do a single **approved known fixture** smoke test via this new API (e.g.
  FSDL) to verify integration with your local QEMU/SSH environment.
- The runner makes no changes to production security policy, so repeating all
  three fixture VMs is unnecessary for this add-only orchestration patch.
- Use teacher repositories only after review and profile preparation. A live
  brand-new repo is guaranteed a *plan*, not guaranteed VM execution.

## Limitations / non-goals

- This is not arbitrary untrusted-submission authorization. The D5e
  `phase3_untrusted_execution_integration = BLOCKED` decision remains true.
- This does not support every executable stack. The existing Phase 2C adapter
  shapes, offline lockfiles, fixed entrypoints and SHA-pinned assets still apply.
- HTTP 200 tests readiness, not correctness or full test-suite success.
- QEMU/WHPX isolation, all real-build descendants and all guest filesystem
  writes have not been independently attested for arbitrary malicious code.
- The synchronous call takes as long as the existing VM and optional prep do;
  it is **one workflow call**, not instant execution.
