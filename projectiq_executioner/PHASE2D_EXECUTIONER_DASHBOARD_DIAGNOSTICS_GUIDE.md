# ProjectIQ — One-Call Executioner Dashboard Diagnostics

**Scope:** Additive diagnostics for the existing one-call execution agent. No change to QEMU, VM admission, SHA allowlisting, network policy, resource limits, package preparation, or repository ingestion limits.

**Why:** A large public Flask repository (FinQuest) returned only `ORCHESTRATION_ERROR` and `ANALYSIS_ERROR`, obscuring the verified source failure: `GitHubClient.download()` rejected the repository ZIP for exceeding its **30 MiB compressed archive limit**. The report now retains the broad orchestration status for existing clients but attaches stable, readable issue details.

## Files

- `projectiq_executioner/phase2/phase2d/agent_diagnostics.py` — closed, reviewed issue catalogue and exception/result classifier. Displays **no raw exception strings**.
- `projectiq_executioner/phase2/phase2d/executioner_agent.py` — adds `issues: []` to the one-call result; populates it on known blocks, exceptions, and unverified VM results; shows concise remediation in the CLI.
- `projectiq_executioner/tests/test_phase2d_executioner_agent.py` — tests exact ZIP limit classification; GitHub access, network, invalid URL, archive hazards, approval, dependencies, startup, cleanup and redaction.
- This guide.

## Apply (PowerShell at ProjectIQ root)

The original one-call agent and its Node profile test fix must already be present. Preserve any other uncommitted work; apply only when `git apply --check` succeeds.

```powershell
$patch = "$env:USERPROFILE\Downloads\projectiq_executioner_agent_dashboard_diagnostics.patch"
git apply --check --verbose $patch
if ($LASTEXITCODE -ne 0) { throw 'Dashboard diagnostics patch does not apply' }
git apply $patch
if ($LASTEXITCODE -ne 0) { throw 'Dashboard diagnostics patch failed' }
git diff --check
if ($LASTEXITCODE -ne 0) { throw 'Whitespace validation failed' }

python -m unittest projectiq_executioner.tests.test_phase2d_executioner_agent -q
if ($LASTEXITCODE -ne 0) { throw 'Executioner agent offline tests failed' }
```

## Verify real FinQuest error (no VM)

**Use a fresh output path.** The orchestrator intentionally refuses to overwrite an existing report.

```powershell
$out = 'outputs/executioner/finquest_diagnostic_02'
python -m projectiq_executioner.phase2.phase2d.executioner_agent `
    --repo 'https://github.com/pr-th/FinQuest.git' `
    --ref 'ccfbe8e736fa4aa859568d0289a7983cd3c65b1f' `
    --vm-config vm_config.json `
    --out $out

# Nonzero exit code is EXPECTED: ZIP is rejected before VM startup.
$r = Get-Content "$out/orchestration_result.json" -Raw | ConvertFrom-Json
$r | Select-Object status, failure_code, execution, cleanup
$r.issues | Format-List code,stage,title,message,recommended_action
$r.steps | Format-Table name,status
if ($r.failure_code -ne 'REPOSITORY_ZIP_TOO_LARGE' -or
    $r.execution -ne 'NOT_PERFORMED' -or $r.cleanup -ne 'NOT_STARTED') {
    throw 'Unexpected FinQuest diagnostic; inspect report'
}
```

For a confirmed large ZIP, expected `status: ORCHESTRATION_ERROR` (backward compatibility) and `failure_code: REPOSITORY_ZIP_TOO_LARGE`. It does **not** pretend to detect Flask dependencies before source analysis; those are subsequent checks after a safe, smaller repository is supplied.

## Stable dashboard contract

A non-successful result includes a nonempty `issues` array. Each issue contains **fixed, non-project-controlled strings**:

```json
{
  "schema_version": "0.1",
  "status": "ORCHESTRATION_ERROR",
  "failure_code": "REPOSITORY_ZIP_TOO_LARGE",
  "execution": "NOT_PERFORMED",
  "cleanup": "NOT_STARTED",
  "issues": [
    {
      "code": "REPOSITORY_ZIP_TOO_LARGE",
      "stage": "analysis",
      "severity": "error",
      "category": "source_limit",
      "audience": "submitter",
      "retryable": false,
      "title": "Repository download exceeds 30 MiB",
      "message": "The GitHub source archive is larger than the Executioner ingestion limit. The project was not analyzed or executed.",
      "recommended_action": "Remove committed virtual environments, generated files, and binary build artifacts from Git, then submit a new commit."
    }
  ]
}
```

The report also retains `steps` for stage-by-stage dashboard progress; `next_action` remains supported for automated orchestrators. Successful runs have `issues: []`.

Dashboard rendering suggestion: show `issue.title` as the main failure heading, `issue.message` below it, and `issue.recommended_action` as the suggested remedy; use `issue.code` for filtering, analytics, and localization. Show `stage` and `category` in the diagnostic panel. Use `audience` to separate student-safe remediation from operator-only infrastructure instructions. Never show internal raw exception text or VM logs to students.

## Coverage and boundaries

- Repository input: unsupported URL, private/inaccessible repo or SHA, GitHub rate limit/access block, network interruption, invalid API/redirect response.
- Source ZIP: download >30 MiB; expanded archive >120 MiB; >12,000 files; individual file >20 MiB; unsafe archive member/symlink/path; corrupt archive.
- Planning: no supported adapter; approval missing for an exact SHA; offline dependency profile missing/invalid; explicit network preparation not authorized; missing pinned Node/pip artifacts.
- VM results: cleanup unverified (highest priority); identity/adapter/backend/attestation mismatch; missing HTTP 200; startup not verified.
- Unknown errors use generic, redacted stage codes. Exception messages, Windows paths, secrets, repository-derived text and raw logs are not written to dashboard issues.

**Important:** A nonzero CLI exit on a failed submission is correct. Do not relax the 30 MiB ZIP limit or bypass GitHub SHA approval merely to make FinQuest pass. Cleaning a repository means removing *tracked* generated dependencies from Git and making a new commit; editing `.gitignore` alone does not remove already-tracked files.

## Regression policy

This patch only changes diagnostics/reporting. It does not modify any VM launch or dependency-preparation controls. Run the focused agent unit tests; **do not rerun the three-fixture VM regression** for this diagnostics-only change. An optional successful static approved-fixture smoke test is unnecessary if the preceding one-call FSDL smoke test already passed and the runtime has not changed.
