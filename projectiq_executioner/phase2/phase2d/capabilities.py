"""Non-executing Phase 2D runtime capability inventory.

This inventory is a planning *gap analysis*, never launch authorization.
Service names, technologies, paths and package scripts are untrusted metadata;
none may become a shell command or host-side import.
"""
from __future__ import annotations

from typing import Any, Mapping

from ..vm_worker.contracts import VMPlanError, build_vm_plan


# Versioned runtime *families* here are design targets, not implemented adapters.
# Keep profiles separate from the Phase 2C execution allowlist.
_TARGETS = {
    'static_web': ('static_html', 'none'),
    'php': ('php_mysql', 'prebaked_php_mysql'),
    'node_frontend': ('node_spa', 'node_lockfile_and_offline_package_cache'),
    'nextjs': ('node_ssr', 'node_lockfile_and_offline_package_cache'),
    'nuxt': ('node_ssr', 'node_lockfile_and_offline_package_cache'),
    'sveltekit': ('node_ssr', 'node_lockfile_and_offline_package_cache'),
    'angular': ('node_spa', 'node_lockfile_and_offline_package_cache'),
    'node': ('node_api', 'node_lockfile_and_offline_package_cache'),
    'python': ('python_web', 'python_version_pinned_offline_wheelhouse'),
    'sqlite': ('sqlite_file', 'isolated_ephemeral_database_file'),
    'mysql': ('mysql_server', 'isolated_disposable_database'),
    'mariadb': ('mysql_server', 'isolated_disposable_database'),
    'postgresql': ('postgresql_server', 'approved_offline_database_profile'),
    'mongodb': ('mongodb_server', 'approved_offline_database_profile'),
    'aws_s3': ('external_cloud', 'local_emulator_or_explicit_mock_required'),
    'remote_http_api': ('external_service', 'local_emulator_or_explicit_mock_required'),
}


def build_capability_plan(manifest: Mapping[str, Any], phase2a: Mapping[str, Any],
                          phase2b: Mapping[str, Any], phase2c: Mapping[str, Any]) -> dict:
    """Return a deterministic, non-authorizing Phase 2D capability gap report.

    Recompiles and compares the Phase 2C candidate to reject a modified
    provenance/adapter input. No project files or source scripts are read.
    """
    expected = build_vm_plan(manifest, phase2a, phase2b)
    if not isinstance(phase2c, Mapping) or any(
        phase2c.get(k) != expected.get(k) for k in
        ('source', 'adapter', 'status', 'probe_path', 'schema_path', 'project_root', 'python_framework', 'vm_limits')
    ):
        raise VMPlanError('Phase 2C candidate differs from verified plan')

    services = phase2b['logical_services']
    pilot = expected['adapter'] is not None
    records = []
    for entry in sorted(services, key=lambda s: s['name']):
        family = entry['family']
        kind = entry['kind']
        profile, dependency_policy = _TARGETS.get(family, ('unknown', 'operator_review_required'))
        if kind == 'external_dependency':
            state = 'EXTERNAL_SERVICE_NOT_AUTHORIZED'
        elif family in ('mysql', 'mariadb') and pilot and expected['adapter'] == 'php_mysql_v1':
            state = 'PHASE2C_CURATED_PILOT_DB_RESOURCE'
        elif kind == 'database' and family == 'sqlite' and expected['adapter'] == 'python_web_offline_v1':
            state = 'PHASE2D_PYTHON_SQLITE_EPHEMERAL_GUEST_ONLY'
        elif kind == 'database':
            state = 'PHASE2D_DB_RESOURCE_NOT_IMPLEMENTED'
        elif pilot and family == 'node_frontend' and expected['adapter'] == 'node_vite_offline_v1':
            state = 'PHASE2D_NODE_VITE_OFFLINE_ASSETS_REQUIRED'
        elif pilot and family == 'node' and expected['adapter'] == 'node_express_offline_v1':
            state = 'PHASE2D_NODE_EXPRESS_OFFLINE_ASSETS_REQUIRED'
        elif pilot and family == 'python' and expected['adapter'] == 'python_web_offline_v1':
            state = 'PHASE2D_PYTHON_OFFLINE_WHEELHOUSE_REQUIRED'
        elif pilot and family in ('static_web', 'php'):
            state = 'PHASE2C_CURATED_PILOT_ADAPTER'
        elif profile == 'unknown':
            state = 'UNKNOWN_RUNTIME_NEEDS_REVIEW'
        else:
            state = 'PHASE2D_ADAPTER_NOT_IMPLEMENTED'
        records.append({
            'name': entry['name'], 'kind': kind, 'root': entry['root'],
            'family': family, 'depends_on': entry['depends_on'],
            'proposed_profile': profile, 'dependency_policy_target': dependency_policy,
            'implementation_state': state,
        })
    outstanding = sorted({r['proposed_profile'] for r in records
                          if r['implementation_state'] in (
                              'PHASE2D_ADAPTER_NOT_IMPLEMENTED',
                              'UNKNOWN_RUNTIME_NEEDS_REVIEW',
                              'EXTERNAL_SERVICE_NOT_AUTHORIZED',
                              'PHASE2D_DB_RESOURCE_NOT_IMPLEMENTED')})
    return {
        'schema_version': '0.1', 'phase': 2, 'stage': 'phase2d_capability_plan_only',
        'source': expected['source'], 'execution': 'NOT_PERFORMED',
        'launch_authorization': 'DENIED_UNTIL_OPERATOR_APPROVAL_AND_RUNTIME_GATES',
        'status': ('PHASE2D_OFFLINE_NODE_ADAPTER_CONDITIONAL'
                   if expected['adapter'] in ('node_vite_offline_v1', 'node_express_offline_v1') else
                   'PHASE2D_OFFLINE_PYTHON_ADAPTER_CONDITIONAL'
                   if expected['adapter'] == 'python_web_offline_v1' else
                   'PHASE2C_PILOT_ADAPTER_AVAILABLE' if pilot else 'NEEDS_PHASE2D_IMPLEMENTATION'),
        'existing_pilot_adapter': (expected['adapter']
                                   if expected['adapter'] in ('static_html_v1', 'php_mysql_v1') else None),
        'phase2d_adapter': (expected['adapter'] if expected['adapter'] in ('node_vite_offline_v1', 'node_express_offline_v1', 'python_web_offline_v1') else None),
        'services': records, 'missing_profiles': outstanding,
        'execution_constraints': [
            'Only pinned operator-reviewed snapshots may execute in the Phase 2C pilot.',
            'No student-defined build, install, package, or README command runs on the host.',
            'Node/Vite execution requires operator-pinned official Linux Node.js and Linux npm cache archives.',
            'Offline npm uses --ignore-scripts; only reviewed Vite build or fixed Express server entrypoints execute in the VM.',
            'Python 3.12 uses a SHA-pinned pip wheel and offline binary-only wheelhouse in an isolated target directory.',
            'Python pilot requires simple exact-version requirements and fixed Flask/FastAPI entrypoint conventions.',
            'External cloud APIs and real secrets are not authorized or automatically contacted.',
            'VM egress stays denied; startup success does not establish sandbox attestation.',
        ],
        'next_focus': [
            'node_spa_offline_dependency_support',
            'python_web_offline_dependency_support',
            'node_express_offline_backend_support',
            'multi_service_readiness_and_teardown',
            'runtime_security_acceptance',
        ],
    }
