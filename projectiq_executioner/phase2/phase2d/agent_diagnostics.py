"""Safe, versioned issue vocabulary for the one-call Executioner dashboard.

Never render raw exception strings, source-controlled README commands, host
paths, account identifiers or process output as a dashboard diagnostic.
"""
from __future__ import annotations

from ...github_client import IngestionError
from ...safe_archive import ArchiveError
from ..vm_worker.contracts import VMPlanError
from ..vm_worker.offline_assets import OfflineAssetError

# Format: category, audience, retryable, title, explanation, next step.
# This is a closed catalogue: all displayed text is trusted and fixed.
_ISSUES = {
    'REPOSITORY_ZIP_TOO_LARGE': (
        'source_limit', 'submitter', False, 'Repository download exceeds 30 MiB',
        'The GitHub source archive is larger than the Executioner ingestion limit. The project was not analyzed or executed.',
        'Remove committed virtual environments, generated files, and binary build artifacts from Git, then submit a new commit.'),
    'REPOSITORY_EXPANDED_TOO_LARGE': (
        'source_limit', 'submitter', False, 'Repository expands beyond 120 MiB',
        'The source archive exceeds the extraction budget even if its compressed ZIP is small.',
        'Remove generated dependencies and unnecessary large files; submit a smaller snapshot.'),
    'REPOSITORY_TOO_MANY_FILES': (
        'source_limit', 'submitter', False, 'Repository contains too many files',
        'The source archive exceeds the 12,000-file extraction limit.',
        'Do not commit local virtual environments or dependency folders; submit a cleaned repository.'),
    'REPOSITORY_FILE_TOO_LARGE': (
        'source_limit', 'submitter', False, 'Individual source file is too large',
        'An archive member exceeds the 20 MiB per-file extraction limit.',
        'Remove oversized generated or binary artifacts and submit a new commit.'),
    'REPOSITORY_ARCHIVE_UNSAFE': (
        'source_security', 'operator', False, 'Repository archive failed security validation',
        'The snapshot contains an unsafe path, symlink, conflicting name or unsupported file type.',
        'Inspect the repository structure; do not bypass archive validation.'),
    'REPOSITORY_ARCHIVE_INVALID': (
        'source_integrity', 'submitter', True, 'Repository archive cannot be read safely',
        'The GitHub snapshot is malformed, incomplete or unsupported.',
        'Retry from a clean commit; contact the operator if the problem persists.'),
    'GITHUB_INVALID_URL': (
        'input', 'submitter', False, 'Invalid GitHub repository URL',
        'Only public GitHub repository-root URLs in the form https://github.com/owner/repo are accepted.',
        'Provide the public repository root URL, without a branch path, query or fragment.'),
    'GITHUB_REPOSITORY_UNAVAILABLE': (
        'source_access', 'submitter', False, 'GitHub repository or commit is inaccessible',
        'GitHub could not locate the selected public repository or commit, or access was denied.',
        'Confirm the repository is public and the URL and commit SHA are correct.'),
    'GITHUB_RATE_LIMITED_OR_FORBIDDEN': (
        'source_access', 'operator', True, 'GitHub access is restricted or rate-limited',
        'GitHub returned an access or API rate-limit response; this is not an application startup failure.',
        'Check GitHub credentials/API limits and retry when access is restored.'),
    'GITHUB_NETWORK_FAILURE': (
        'network', 'operator', True, 'Could not reach GitHub',
        'The repository fetch failed due to a connection or timeout error.',
        'Check the controller network connection and retry the same pinned commit.'),
    'GITHUB_RESPONSE_INVALID': (
        'source_integrity', 'operator', True, 'GitHub returned an invalid response',
        'The GitHub metadata or redirected download response failed validation.',
        'Check GitHub availability and retry; never disable the restricted redirect policy.'),
    'NO_SUPPORTED_ADAPTER': (
        'compatibility', 'submitter', False, 'No executable adapter matches this repository',
        'ProjectIQ could analyze the source, but it could not select a supported runtime adapter.',
        'Inspect the Phase 2C plan and adapt the project to a supported layout or implement a reviewed adapter.'),
    'PINNED_SHA_NOT_APPROVED': (
        'authorization', 'operator', False, 'This exact repository commit has not been approved',
        'The runner intentionally blocks executing unreviewed student code.',
        'Review the pinned GitHub snapshot and explicitly approve its owner/repository@SHA in operator configuration.'),
    'PREPARATION_NOT_ENABLED': (
        'dependencies', 'operator', False, 'Offline runtime dependencies are missing or invalid',
        'The required SHA-pinned runtime/cache profile could not be verified and automatic preparation is disabled.',
        'Check or prepare the approved offline assets; do not remove hash verification.'),
    'TRUSTED_DEPENDENCY_NETWORK_NOT_AUTHORIZED': (
        'authorization', 'operator', False, 'Dependency preparation network is not authorized',
        'The separate preparation VM cannot access package registries without explicit operator permission.',
        'Authorize trusted dependency preparation only after reviewing the exact repository SHA.'),
    'PINNED_NODE_ARTIFACT_REQUIRED': (
        'dependencies', 'operator', False, 'Pinned Linux Node runtime is required',
        'The approved Node archive and SHA-256 were not supplied for dependency preparation.',
        'Provide a verified official Linux Node archive and its independently checked digest.'),
    'PINNED_PIP_ARTIFACT_REQUIRED': (
        'dependencies', 'operator', False, 'Pinned pip wheel is required',
        'The approved pip wheel and SHA-256 were not supplied for Python dependency preparation.',
        'Provide the reviewed pip wheel and matching independently checked digest.'),
    'ADAPTER_NOT_SUPPORTED_FOR_PREPARATION': (
        'compatibility', 'operator', False, 'Dependency preparation is not available for this adapter',
        'The agent has no approved automatic preparation path for the selected adapter.',
        'Review adapter support before enabling the execution stage.'),
    'OFFLINE_DEPENDENCY_VALIDATION_FAILED': (
        'dependencies', 'operator', False, 'Offline asset validation failed',
        'An approved runtime or package cache failed the required integrity/profile checks.',
        'Inspect the dependency profile and its expected digests, then prepare a valid profile.'),
    'VM_CLEANUP_UNVERIFIED': (
        'security', 'operator', False, 'Disposable VM cleanup could not be verified',
        'The Executioner cannot confirm that the VM stopped and temporary storage was removed.',
        'Stop new VM runs and inspect the managed runtime workspace before retrying.'),
    'VM_EVIDENCE_INCONSISTENT': (
        'security', 'operator', False, 'Execution identity or isolation evidence is inconsistent',
        'The VM result does not match the requested source, adapter, backend or required security contract.',
        'Inspect the VM execution report; do not treat this attempt as a verified startup.'),
    'VM_HTTP_NOT_READY': (
        'startup', 'operator', True, 'Application did not pass its HTTP readiness check',
        'The expected HTTP 200 readiness result was not verified inside the disposable VM.',
        'Inspect the guest startup report and the selected adapter entrypoint.'),
    'VM_STARTUP_UNVERIFIED': (
        'startup', 'operator', True, 'VM application startup was not verified',
        'The approved worker did not return a fully verified startup result.',
        'Inspect the VM report for a supported failure code; do not assume execution succeeded.'),
    'ANALYSIS_ERROR': (
        'internal', 'operator', False, 'Repository analysis could not be completed',
        'An unexpected failure interrupted analysis; details are withheld from the student dashboard.',
        'Review trusted operator diagnostics without exposing local paths or secrets.'),
    'ADAPTER_ERROR': (
        'internal', 'operator', False, 'Adapter selection failed unexpectedly',
        'The planner could not produce a verified execution candidate.',
        'Inspect trusted controller diagnostics and the saved planning artifacts.'),
    'OPERATOR_APPROVAL_ERROR': (
        'configuration', 'operator', False, 'VM authorization configuration could not be checked',
        'The operator-owned VM configuration or approval check failed.',
        'Review the local VM configuration and approved SHA list.'),
    'OFFLINE_DEPENDENCIES_ERROR': (
        'dependencies', 'operator', False, 'Dependency preparation failed',
        'The approved dependency setup did not finish with verified artifacts.',
        'Inspect the trusted preparation report; do not skip artifact integrity checks.'),
    'VM_EXECUTION_ERROR': (
        'internal', 'operator', False, 'VM execution encountered an unexpected error',
        'The execution controller could not complete its run or verify cleanup.',
        'Inspect trusted worker diagnostics and verify no orphan VM is left running.'),
}


def issue(code: str, stage: str) -> dict:
    """Construct a display-safe and machine-readable error from fixed values."""
    if code not in _ISSUES:
        raise ValueError('Unknown dashboard issue code')
    category, audience, retryable, title, message, action = _ISSUES[code]
    return {
        'code': code, 'stage': stage, 'severity': 'error',
        'category': category, 'audience': audience, 'retryable': retryable,
        'title': title, 'message': message, 'recommended_action': action,
    }


def classify_error(stage: str, exc: Exception) -> str:
    """Categorize only exact, known error messages; never publish `str(exc)`."""
    if isinstance(exc, IngestionError):
        msg = str(exc)
        if msg == 'Repository ZIP exceeds the configured download size limit.':
            return 'REPOSITORY_ZIP_TOO_LARGE'
        if msg in (
            'Expected an HTTPS GitHub repository URL: https://github.com/owner/repo',
            'Only repository root URLs are accepted (no subpaths or encoded segments).',
            'Invalid GitHub repository owner/name.',
        ):
            return 'GITHUB_INVALID_URL'
        if msg in (
            'GitHub repository/ref not found, private, or inaccessible.',
            'Private repositories are not supported in Phase 1.',
        ):
            return 'GITHUB_REPOSITORY_UNAVAILABLE'
        if msg == 'GitHub request was rate-limited or forbidden. Set GITHUB_TOKEN for API requests, or try later.':
            return 'GITHUB_RATE_LIMITED_OR_FORBIDDEN'
        if msg.startswith('Network request failed: ') and len(msg) < 130:
            return 'GITHUB_NETWORK_FAILURE'
        if msg in (
            'Unexpected redirect outside approved GitHub download hosts.',
            'GitHub API response exceeds size limit.', 'Invalid GitHub API JSON response.',
            'Unexpected GitHub response type.', 'GitHub failed to return a full commit SHA.',
        ) or (msg.startswith('GitHub returned HTTP ') and msg.endswith('.') and len(msg) < 40):
            return 'GITHUB_RESPONSE_INVALID'
        return 'ANALYSIS_ERROR'
    if isinstance(exc, ArchiveError):
        msg = str(exc)
        if msg == 'Archive exceeds expanded size limit.':
            return 'REPOSITORY_EXPANDED_TOO_LARGE'
        if msg == 'Archive contains too many files.':
            return 'REPOSITORY_TOO_MANY_FILES'
        if msg == 'Archive member exceeds per-file size limit.':
            return 'REPOSITORY_FILE_TOO_LARGE'
        if msg in {
            'Unsafe raw ZIP member filename.', 'Unsafe archive path encountered.',
            'Archive contains a traversal or drive path.',
            'Archive contains symlinks; refusing extraction.',
            'Archive contains unsupported file types.',
            'Duplicate/case-colliding archive paths.',
            'ZIP local filename is too long.',
        }:
            return 'REPOSITORY_ARCHIVE_UNSAFE'
        return 'REPOSITORY_ARCHIVE_INVALID'
    if isinstance(exc, OfflineAssetError):
        # Includes unrecognized error messages. Do not expose their contents.
        return 'OFFLINE_DEPENDENCY_VALIDATION_FAILED'
    if isinstance(exc, VMPlanError) and stage == 'operator_approval':
        return 'OPERATOR_APPROVAL_ERROR'
    return {
        'analysis': 'ANALYSIS_ERROR', 'adapter': 'ADAPTER_ERROR',
        'operator_approval': 'OPERATOR_APPROVAL_ERROR',
        'offline_dependencies': 'OFFLINE_DEPENDENCIES_ERROR',
        'vm_execution': 'VM_EXECUTION_ERROR',
    }.get(stage, 'ANALYSIS_ERROR')


def classify_vm_result(vm: dict, *, requested_source: dict,
                       requested_adapter: str, clean: str, attestation: str) -> str:
    """Select the most actionable reason without trusting raw guest messages."""
    if vm.get('cleanup') != clean:
        return 'VM_CLEANUP_UNVERIFIED'
    if (vm.get('source') != requested_source or
            vm.get('adapter') != requested_adapter or
            vm.get('backend') != 'QEMU_DISPOSABLE_QCOW2_VM' or
            vm.get('security_attestation') != attestation):
        return 'VM_EVIDENCE_INCONSISTENT'
    guest = vm.get('guest_result')
    if type(guest) is dict and (type(guest.get('http_status')) is int and
                                guest['http_status'] != 200):
        return 'VM_HTTP_NOT_READY'
    return 'VM_STARTUP_UNVERIFIED'
