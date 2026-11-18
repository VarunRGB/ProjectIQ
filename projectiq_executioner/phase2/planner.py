"""Phase 2A: inert, conservative sandbox execution planning.

No subprocess, container runtime, network call, install, import of student
code, or shell command is performed here. A later launcher must enforce the
sandbox controls and reject plans that cannot satisfy them.
"""
from __future__ import annotations

import json
import re
from pathlib import Path, PurePosixPath
from typing import Any, Mapping


class InvalidManifest(ValueError):
    pass


def _safe_name(value: Any, fallback: str) -> str:
    if isinstance(value, str) and re.fullmatch(r"[A-Za-z][A-Za-z0-9_-]{0,60}", value):
        return value
    return fallback


def _safe_root(value: Any) -> str | None:
    if value == '.':
        return '.'
    if not isinstance(value, str) or not value or '\\' in value or '\x00' in value:
        return None
    p = PurePosixPath(value)
    if p.is_absolute() or any(part in ('..', '.') for part in value.split('/')):
        return None
    if not re.fullmatch(r'[a-zA-Z0-9_./-]{1,180}', value):
        return None
    return value


def _service_strategy(kind: str, tech: str) -> str:
    stack = tech.lower()
    if kind == 'external_dependency':
        return 'mock_or_authorized_isolated_test_service'
    if kind == 'database':
        return 'disposable_embedded_storage' if 'sqlite' in stack else 'isolated_database_container_candidate'
    if kind == 'frontend':
        if any(x in stack for x in ('react', 'next', 'vue', 'svelte', 'angular')):
            return 'isolated_node_container_candidate'
        return 'isolated_web_container_candidate'
    if kind == 'backend':
        if any(x in stack for x in ('fastapi', 'flask', 'django')):
            return 'isolated_python_container_candidate'
        if 'php' in stack:
            return 'isolated_php_container_candidate'
        if any(x in stack for x in ('express', 'node', 'nestjs', 'fastify')):
            return 'isolated_node_container_candidate'
        return 'isolated_runtime_container_candidate'
    return 'manual_review_required'


def build_execution_plan(manifest: Mapping[str, Any]) -> dict[str, Any]:
    """Produce a non-executable, source-grounded plan from Phase 1 metadata."""
    if manifest.get('schema_version') != '1.0' or manifest.get('phase') != 1:
        raise InvalidManifest('Expected a Phase 1 manifest with schema_version 1.0.')
    source = manifest.get('source')
    if not isinstance(source, dict):
        raise InvalidManifest('source metadata missing')
    source_kind = source.get('kind')
    if source_kind not in ('github', 'local'):
        raise InvalidManifest('unsupported source kind')
    services = manifest.get('services')
    if not isinstance(services, list) or len(services) > 100:
        raise InvalidManifest('services must be a bounded list')
    inventory = manifest.get('inventory', {})
    if not isinstance(inventory, dict):
        inventory = {}
    env_details = inventory.get('environment_variable_details', {})
    if not isinstance(env_details, dict):
        env_details = {}

    blockers: list[str] = []
    sha = source.get('commit_sha')
    if source_kind == 'github' and (not isinstance(sha, str) or not re.fullmatch('[0-9a-fA-F]{40}', sha)):
        blockers.append('MISSING_PINNED_GITHUB_COMMIT')
    topology = []
    external_names = []
    for i, service in enumerate(services):
        if not isinstance(service, dict):
            raise InvalidManifest('invalid service record')
        kind = service.get('kind')
        if kind not in {'frontend', 'backend', 'database', 'external_dependency', 'worker', 'orchestration'}:
            kind = 'unknown'
        name = _safe_name(service.get('name'), f'service_{i+1}')
        tech = service.get('technology') if isinstance(service.get('technology'), str) else 'unknown'
        tech = tech[:100]  # descriptive metadata only; never a command
        root = ('external' if kind == 'external_dependency' else _safe_root(service.get('root')))
        if root is None:
            blockers.append('UNSAFE_OR_UNRESOLVED_SERVICE_ROOT')
        dependency_names = [v for v in service.get('depends_on', [])
                            if isinstance(v, str) and re.fullmatch('[A-Za-z][A-Za-z0-9_-]{0,60}', v)] if isinstance(service.get('depends_on'), list) else []
        topology.append({
            'name': name, 'kind': kind, 'technology_hint': tech,
            'source_root': root, 'depends_on': dependency_names,
            'strategy': _service_strategy(kind, tech),
            'readiness': 'UNVERIFIED', 'startup': 'NOT_ATTEMPTED',
        })
        if kind == 'external_dependency':
            external_names.append(name)
    if external_names:
        blockers.append('EXTERNAL_DEPENDENCIES_REQUIRE_MOCKS_OR_ISOLATED_AUTHORIZED_TEST_RESOURCES')
    if not topology:
        blockers.append('NO_RUNNABLE_SERVICES_IDENTIFIED')

    env_names = manifest.get('environment_variables', [])
    if not isinstance(env_names, list):
        env_names = []
    env_inventory = []
    for name in sorted(set(v for v in env_names if isinstance(v, str))):
        if not re.fullmatch('[A-Za-z_][A-Za-z0-9_]{0,100}', name):
            continue
        details = env_details.get(name, {})
        if not isinstance(details, dict):
            details = {}
        required = details.get('required_hint') if isinstance(details.get('required_hint'), bool) else None
        env_inventory.append({'name': name, 'required_hint': required,
                              'value_available': False, 'value_source': 'NOT_LOADED'})
    if any(x['required_hint'] is True for x in env_inventory):
        blockers.append('REQUIRED_CONFIGURATION_MUST_BE_INJECTED_AS_SYNTHETIC_OR_TEST_SECRETS')

    api_routes = manifest.get('routes', [])
    health_candidates = []
    if isinstance(api_routes, list):
        for route in api_routes[:3000]:
            if not isinstance(route, dict):
                continue
            if (route.get('method') == 'GET'
                and route.get('kind') in {'declared_http_route', 'file_based_route'}
                and isinstance(route.get('path'), str)
                and re.fullmatch(r'/[a-zA-Z0-9_/-]*(?:health|healthz|ready|readiness|ping)', route['path'], re.I)):
                health_candidates.append(route['path'])
    # No port assumptions: environment orchestration must discover the port.
    hints = sorted(set(health_candidates))[:12]

    return {
        'schema_version': '0.1', 'phase': 2, 'stage': 'plan_only',
        'execution': 'NOT_PERFORMED', 'readiness': 'NOT_VERIFIED',
        'status': 'BLOCKED' if blockers else 'PLAN_ONLY',
        'source': {'kind': source_kind, 'repository': source.get('repository'),
                   'commit_sha': sha if source_kind == 'github' else None},
        'proposed_services': topology,
        'environment_variables': env_inventory,
        'health_endpoint_candidates': hints,
        'blocking_conditions': sorted(set(blockers)),
        'sandbox_policy_requirements': {
            'rootless_containers': True, 'privileged_containers': False,
            'host_docker_socket_mounts': False, 'host_filesystem_write_mounts': False,
            'source_checkout': 'read_only_in_sandbox',
            'writable_data': 'disposable_ephemeral_volumes',
            'network': 'deny_external_egress_by_default; isolated_interservice_only',
            'secrets': 'isolated_test_credentials_only; never reuse production secrets',
            'resource_limits': 'required; choose tested CPU, memory, PID and time limits',
            'teardown': 'mandatory_even_on_failure',
        },
        'next_steps': [
            'Choose allowlisted runtime images and verify a container isolation boundary',
            'Provide synthetic test environment and isolated database/storage dependencies',
            'Prepare sandbox-only build/start steps; never execute README or package commands on host',
            'Create health/readiness probes with discovered ports and timeouts',
            'Implement teardown and capture diagnostics before permitting any execution',
        ],
        'limitations': [
            'A plan is not a runnable container specification and cannot establish safety or readiness.',
            'Untrusted project code was not installed, started, built, or tested.',
            'External services must never be contacted or mutated merely because an API URL exists.',
            'Expected test behavior and functional evaluation belong to Phases 3-4.',
        ],
    }


def plan_from_file(input_path: Path, output_path: Path) -> dict[str, Any]:
    infile = Path(input_path)
    if infile.stat().st_size > 8_000_000:
        raise InvalidManifest('Manifest exceeds 8 MB input cap')
    manifest = json.loads(infile.read_text(encoding='utf-8'))
    if not isinstance(manifest, dict):
        raise InvalidManifest('Manifest root must be a JSON object')
    plan = build_execution_plan(manifest)
    target = Path(output_path)
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(json.dumps(plan, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
    return plan
