# ProjectIQ Executioner - repeatable D5/B3b Gate 2A.3 regression
# Runs offline tests, then the three SHA-pinned, operator-approved fixture VMs.
# Evidence is always written under outputs/executioner; VM outputs must not be committed.
# Usage (from the ProjectIQ root):
#   & .\outputs\executioner\tools\projectiq_gate2a3_regression.ps1
# Or:
#   & "$HOME\Downloads\projectiq_gate2a3_regression.ps1" -RepoRoot "C:\Users\varun\Code\ProjectIQ"
param(
    [string]$RepoRoot = ".",
    [string]$OutputBase = "outputs/executioner",
    [string]$Python = "python",
    [string]$ExpectedBranch = "executioner"
)

Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'

function Assert-ProjectIQ([bool]$Condition, [string]$Message) {
    if (-not $Condition) { throw $Message }
}

$runId = (Get-Date -Format 'yyyyMMdd_HHmmss') + '_' + ([guid]::NewGuid().ToString('N').Substring(0, 6))
$results = @()
$failure = $null
$allPassed = $false
$runRoot = $null

Push-Location (Resolve-Path -LiteralPath $RepoRoot).Path
try {
    Assert-ProjectIQ (Test-Path -LiteralPath 'projectiq_executioner') 'Not at a ProjectIQ repository root.'
    Assert-ProjectIQ (Test-Path -LiteralPath 'vm_config.json') 'vm_config.json not found.'

    $branch = (& git branch --show-current).Trim()
    Assert-ProjectIQ ($LASTEXITCODE -eq 0) 'Cannot determine Git branch.'
    Assert-ProjectIQ ($branch -eq $ExpectedBranch) "Expected branch '$ExpectedBranch'; found '$branch'."

    & git diff --check
    Assert-ProjectIQ ($LASTEXITCODE -eq 0) 'git diff --check failed.'

    $qemuPath = Join-Path $env:ProgramFiles 'qemu'
    if ((Test-Path -LiteralPath $qemuPath) -and ($env:Path -notlike "*$qemuPath*")) {
        $env:Path += ";$qemuPath"
    }

    $runRoot = Join-Path $OutputBase ("gate2a3_regression_" + $runId)
    New-Item -ItemType Directory -Path $runRoot -Force | Out-Null
    $runRoot = (Resolve-Path -LiteralPath $runRoot).Path

    Write-Host "`nProjectIQ Gate 2A.3 final regression - $runId" -ForegroundColor Cyan
    Write-Host "Results: $runRoot"
    Write-Host "`nRunning the complete offline regression suite..." -ForegroundColor Cyan
    & $Python -m unittest discover -s projectiq_executioner/tests -p 'test_*.py' -q
    Assert-ProjectIQ ($LASTEXITCODE -eq 0) 'Offline unit tests failed. No VM launches attempted.'

    $config = Get-Content -LiteralPath 'vm_config.json' -Raw | ConvertFrom-Json
    $approved = @($config.approved_snapshots)
    $fixtures = @(
        @{
            Name = 'FSDL'; Adapter = 'static_html_v1'; Servers = 1
            Source = 'outputs/executioner/fsdl_phase1/manifest.json'
            Build = $false
        },
        @{
            Name = 'MiniCollabApp'; Adapter = 'php_mysql_v1'; Servers = 1
            Source = 'outputs/executioner/mini_collab_phase1/manifest.json'
            Build = $false
        },
        @{
            Name = 'FullStack'; Adapter = 'node_fullstack_offline_v1'; Servers = 2
            Source = 'outputs/executioner/d4_fullstack_plan/phase2c_candidate.json'
            Build = $true
        }
    )

    foreach ($fixture in $fixtures) {
        $name = [string]$fixture.Name
        $out = Join-Path $runRoot ($name.ToLowerInvariant())
        $reportPath = Join-Path $out 'vm_execution.json'
        $row = [ordered]@{
            Fixture = $name; Startup = 'FAIL'; HTTP = 'UNVERIFIED'
            Servers = 0; BuildEvidence = 'NOT_VERIFIED'
            Cleanup = 'UNVERIFIED'; Report = $reportPath
        }
        try {
            Write-Host "`n=== $name ===" -ForegroundColor Cyan
            Assert-ProjectIQ (Test-Path -LiteralPath $fixture.Source) "Missing fixture metadata: $($fixture.Source)"
            $metadata = Get-Content -LiteralPath $fixture.Source -Raw | ConvertFrom-Json
            $identity = [string]$metadata.source.repository
            $sha = [string]$metadata.source.commit_sha
            Assert-ProjectIQ ($identity -match '^[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+$') "${name}: invalid repository identity."
            Assert-ProjectIQ ($sha -match '^[0-9a-fA-F]{40}$') "${name}: expected an immutable 40-character commit SHA."
            Assert-ProjectIQ ("$identity@$sha" -in $approved) "${name}: pinned snapshot missing from vm_config.json approval list."

            $url = 'https://github.com/' + $identity
            & $Python -m projectiq_executioner.phase2.phase2d `
                --repo $url --ref $sha --vm-config vm_config.json `
                --run-reviewed-fixture --out $out
            $exitCode = $LASTEXITCODE

            Assert-ProjectIQ (Test-Path -LiteralPath $reportPath) "${name}: missing vm_execution.json (Python exit $exitCode)."
            $v = Get-Content -LiteralPath $reportPath -Raw | ConvertFrom-Json
            $row.Cleanup = [string]$v.cleanup
            # Success reports intentionally omit guest_error_code. Access it
            # conditionally to remain compatible with Set-StrictMode.
            $guestError = '(none reported)'
            if ($null -ne $v.PSObject.Properties['guest_error_code']) {
                $guestError = [string]$v.guest_error_code
            }
            Assert-ProjectIQ ($exitCode -eq 0) "${name}: execution failed: $guestError (see $reportPath)."
            Assert-ProjectIQ ($v.adapter -eq $fixture.Adapter) "${name}: unexpected VM adapter."
            Assert-ProjectIQ ($v.status -eq 'STARTUP_VERIFIED' -and
                              $v.execution -eq 'PERFORMED_IN_DISPOSABLE_VM' -and
                              $v.guest_result.status -eq 'STARTUP_VERIFIED') "${name}: startup was not verified."
            Assert-ProjectIQ ($v.cleanup -eq 'VM_STOPPED_DISPOSABLE_STORAGE_REMOVED') "${name}: VM cleanup was not verified."
            Assert-ProjectIQ ($v.security_attestation -eq 'NOT_ATTESTED_FOR_UNTRUSTED_STUDENTS') "${name}: unexpected security attestation."
            Assert-ProjectIQ ($v.guest_result.http_status -eq 200) "${name}: expected HTTP 200."

            $placement = $v.guest_result.cgroup_placement
            Assert-ProjectIQ ($placement.status -eq 'SERVER_PIDS_VERIFIED' -and
                              $placement.server_count -eq $fixture.Servers) "${name}: independent server placement failed."
            Assert-ProjectIQ ($placement.builds_verified -eq $false -and
                              $placement.database_service_verified -eq $false) "${name}: unexpected unverified security claim."

            $build = $v.guest_result.build_launch_evidence
            if ($fixture.Build) {
                Assert-ProjectIQ ($build.status -eq 'PREEXEC_KERNEL_HANDOFF_VERIFIED' -and
                                  $build.npm_ci -eq 2 -and $build.vite_build -eq 1 -and
                                  $build.pip_install -eq 0) 'FullStack: build/install handoff evidence failed.'
                $s = $v.guest_result.service_results
                Assert-ProjectIQ ($s.frontend_http -eq 200 -and $s.backend_http -eq 200 -and
                                  $s.built_asset_http -eq 200 -and $s.frontend_proxy_api_http -eq 200 -and
                                  $s.sqlite_verified -eq $true) 'FullStack: API, asset, or SQLite check failed.'
            }
            else {
                Assert-ProjectIQ ($build.status -eq 'NOT_APPLICABLE' -and
                                  $build.npm_ci -eq 0 -and $build.vite_build -eq 0 -and
                                  $build.pip_install -eq 0) "${name}: unexpected build/install evidence."
            }

            $row.Startup = 'PASS'
            $row.HTTP = '200'
            $row.Servers = [int]$placement.server_count
            $row.BuildEvidence = [string]$build.status
            $row.Cleanup = 'PASS'
            Write-Host "${name}: ALL CHECKS PASSED" -ForegroundColor Green
        }
        finally {
            $results += [pscustomobject]$row
        }
    }

    $allPassed = $true
    Write-Host "`nALL THREE FIXTURES PASSED!" -ForegroundColor Green
}
catch {
    $failure = $_.Exception.Message
    Write-Host "`nREGRESSION FAILED: $failure" -ForegroundColor Red
    throw
}
finally {
    if ($null -ne $runRoot -and (Test-Path -LiteralPath $runRoot)) {
        $summary = [ordered]@{
            RunId = $runId
            Passed = $allPassed
            Failure = $failure
            Results = $results
            SecurityAttestation = 'NOT_ATTESTED_FOR_UNTRUSTED_STUDENTS'
        }
        $jsonPath = Join-Path $runRoot 'regression_summary.json'
        $csvPath = Join-Path $runRoot 'regression_summary.csv'
        $summary | ConvertTo-Json -Depth 6 | Set-Content -LiteralPath $jsonPath -Encoding UTF8
        if ($results.Count -gt 0) { $results | Export-Csv -LiteralPath $csvPath -NoTypeInformation -Encoding UTF8 }
        Write-Host "`nSaved summary: $jsonPath"
        if ($results.Count -gt 0) { $results | Format-Table Fixture,Startup,HTTP,Servers,BuildEvidence,Cleanup -AutoSize }
    }
    Pop-Location
}
