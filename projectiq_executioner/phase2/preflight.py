"""ProjectIQ Phase 2B: architecture-aware, NON-EXECUTING preparation.

Consumes pinned Phase 1 manifest + Phase 2A plan. It performs no checkout,
container build, dependency installation, project import, or project execution.
The JSON is a 2C handoff *proposal*, not a launch-authorizing configuration.
"""
from __future__ import annotations

import argparse
import json
import re
from collections import defaultdict
from pathlib import Path, PurePosixPath
from typing import Any, Mapping

from .planner import InvalidManifest
from .runtime_probe import probe_container_runtime, unprobed_runtime

MAX_INPUT_BYTES = 8_000_000
SAFE_NAME = re.compile(r'^[a-zA-Z][a-zA-Z0-9_-]{0,60}$')
SAFE_ENV = re.compile(r'^[A-Za-z_][A-Za-z0-9_]{0,100}$')
SAFE_PATH = re.compile(r'^[A-Za-z0-9_./@+-]{1,200}$')
SAFE_SHA = re.compile(r'^[a-fA-F0-9]{40}$')


class InvalidPreflightInput(ValueError):
    pass


def safe_path(value: Any, *, allow_dot: bool = True) -> str | None:
    """Lexical validation only. Actual source symlinks must be checked in 2C."""
    if value == '.' and allow_dot:
        return '.'
    if not isinstance(value, str) or not SAFE_PATH.fullmatch(value):
        return None
    p = PurePosixPath(value)
    if p.is_absolute() or '//' in value or any(x in {'.', '..', ''} for x in value.split('/')):
        return None
    return value if str(p) == value else None


def _family(tech: str, kind: str) -> str:
    t = tech.lower()
    if kind == 'database':
        for token, name in [('sqlite', 'sqlite'), ('mariadb', 'mariadb'), ('mysql', 'mysql'),
                            ('postgres', 'postgresql'), ('mongo', 'mongodb'), ('redis', 'redis')]:
            if token in t:
                return name
        return 'unknown_database'
    if kind == 'external_dependency':
        if 's3' in t: return 'aws_s3'
        if 'http' in t or 'api' in t: return 'remote_http_api'
        return 'external_unknown'
    if kind == 'frontend':
        if 'next' in t: return 'nextjs'
        if 'nuxt' in t: return 'nuxt'
        if 'sveltekit' in t: return 'sveltekit'
        if 'angular' in t: return 'angular'
        if any(x in t for x in ('react', 'vue', 'svelte', 'vite')): return 'node_frontend'
        if any(x in t for x in ('html', 'static', 'javascript', 'js frontend')): return 'static_web'
        return 'unknown_frontend'
    if kind in {'backend', 'worker'}:
        if any(x in t for x in ('fastapi', 'flask', 'django', 'python')): return 'python'
        if any(x in t for x in ('node', 'express', 'nestjs', 'fastify', 'koa', 'hapi')): return 'node'
        if any(x in t for x in ('php', 'laravel', 'symfony')): return 'php'
        if any(x in t for x in ('spring', 'java', 'quarkus', 'micronaut')): return 'java'
        if any(x in t for x in ('asp.net', 'dotnet', '.net')): return 'dotnet'
        if any(x in t for x in ('golang', 'go (', 'gin-gonic', 'go/gin', 'go/echo')): return 'go'
        if any(x in t for x in ('ruby', 'rails', 'sinatra')): return 'ruby'
        if any(x in t for x in ('rust', 'actix', 'axum', 'rocket')): return 'rust'
        return 'unknown_runtime'
    return 'unknown_runtime'


PROFILE = {
    'python': ('python', 'python_application', None),
    'node': ('node', 'node_application', None),
    'php': ('php', 'php_application', None),
    'java': ('eclipse-temurin', 'java_application', None),
    'dotnet': ('dotnet', 'dotnet_application', None),
    'go': ('golang', 'go_application', None),
    'ruby': ('ruby', 'ruby_application', None),
    'rust': ('rust', 'rust_application', None),
    'nextjs': ('node', 'nextjs_application', None),
    'nuxt': ('node', 'nuxt_application', None),
    'sveltekit': ('node', 'sveltekit_application', None),
    'angular': ('node', 'angular_application', None),
    'node_frontend': ('node', 'frontend_application', None),
    'static_web': ('nginx', 'static_site', None),
    'mysql': ('mysql', 'database_server', 3306),
    'mariadb': ('mariadb', 'database_server', 3306),
    'postgresql': ('postgres', 'database_server', 5432),
    'mongodb': ('mongo', 'database_server', 27017),
    'redis': ('redis', 'database_server', 6379),
}


_SANDBOX_BASELINE = {
    'execution_host': 'dedicated_disposable_worker_vm_required',
    'runtime': 'rootless_linux_engine_must_be_verified',
    'container_user': 'unprivileged_nonroot_uid',
    'process_privileges': 'no_new_privileges;drop_all_linux_capabilities',
    'security_profiles': 'verified_seccomp_and_apparmor_or_selinux',
    'linux_namespaces': 'isolated_user_pid_mount_ipc_network',
    'root_filesystem': 'read_only_except_explicit_ephemeral_paths',
    'filesystem': 'immutable_pinned_checkout;reject_symlink_escape;never_mount_host_home_or_docker_socket',
    'volumes': 'disposable_scoped_storage_only;no_host_write_bind_mounts',
    'network': 'private_isolated_interservice_only;external_egress_denied;DNS_and_proxy_escape_tested',
    'dependency_install': 'offline_curated_cache_or_approved_build_stage;no_unrestricted_internet',
    'image_policy': 'operator_allowlisted_immutable_sha256_digests;never_trust_student_Dockerfile',
    'resources': {'cpu': 'ENFORCE_AND_MEASURE', 'memory': 'ENFORCE_AND_MEASURE',
                  'pids': 'ENFORCE_AND_MEASURE', 'disk': 'ENFORCE_AND_MEASURE',
                  'network_bytes': 'ENFORCE_AND_MEASURE', 'wall_time': 'ENFORCE_AND_MEASURE'},
    'secrets': 'synthetic_test_only;inject_at_2C_runtime;never_put_values_in_manifest_or_logs',
    'logs': 'bounded_and_redacted;per_run_scoped',
    'teardown': 'kill_cleanup_and_volume_network_removal_must_be_failure_tested',
    'untrusted_compose': 'NEVER_EXECUTE_DIRECTLY',
    'untrusted_package_scripts': 'NEVER_RUN_ON_HOST',
}


def _valid_source(manifest: Mapping[str, Any], plan: Mapping[str, Any]) -> dict[str, Any]:
    if manifest.get('schema_version') != '1.0' or manifest.get('phase') != 1:
        raise InvalidPreflightInput('Expected a Phase 1 schema_version 1.0 manifest')
    if plan.get('phase') != 2 or plan.get('stage') != 'plan_only' or plan.get('execution') != 'NOT_PERFORMED':
        raise InvalidPreflightInput('Expected an unexecuted Phase 2A plan')
    p1source, p2source = manifest.get('source'), plan.get('source')
    if not isinstance(p1source, dict) or not isinstance(p2source, dict):
        raise InvalidPreflightInput('Source information is missing')
    kind = p1source.get('kind')
    if kind not in {'github', 'local'} or p2source.get('kind') != kind:
        raise InvalidPreflightInput('Invalid or mismatched source kinds')
    sha = p1source.get('commit_sha')
    if p2source.get('commit_sha') != sha:
        raise InvalidPreflightInput('Phase 2A and Phase 1 commit SHAs differ')
    if kind == 'github' and (not isinstance(sha, str) or not SAFE_SHA.fullmatch(sha)):
        raise InvalidPreflightInput('Pinned GitHub commit SHA is required')
    repo = p1source.get('repository')
    if p2source.get('repository') != repo:
        raise InvalidPreflightInput('Phase 2A and Phase 1 repository names differ')
    if not isinstance(repo, str) or not re.fullmatch(r'[A-Za-z0-9_.-]{1,100}/[A-Za-z0-9_.-]{1,100}', repo):
        if kind == 'github':
            raise InvalidPreflightInput('Invalid repository identifier')
        repo = '<local-source>'
    return {'kind': kind, 'repository': repo, 'commit_sha': sha if kind == 'github' else None,
            'snapshot_policy': 'verify_exact_SHA_and_file_paths_in_2C_worker'}


def _check_topology(manifest: Mapping[str, Any], plan: Mapping[str, Any]) -> list[dict[str, Any]]:
    a = manifest.get('services')
    b = plan.get('proposed_services')
    if not isinstance(a, list) or not isinstance(b, list) or len(a) != len(b) or len(a) > 100:
        raise InvalidPreflightInput('Phase 1 / 2A topology size mismatch')
    p2 = {}
    for s in b:
        if not isinstance(s, dict) or not isinstance(s.get('name'), str):
            raise InvalidPreflightInput('Invalid Phase 2A service record')
        if s['name'] in p2:
            raise InvalidPreflightInput('Duplicate Phase 2A service name')
        p2[s['name']] = s
    output = []
    names = set()
    for s in a:
        if not isinstance(s, dict):
            raise InvalidPreflightInput('Invalid Phase 1 service record')
        name = s.get('name')
        kind = s.get('kind')
        if not isinstance(name, str) or not SAFE_NAME.fullmatch(name) or name in names:
            raise InvalidPreflightInput('Unsafe/duplicate Phase 1 service name')
        names.add(name)
        if kind not in {'backend', 'frontend', 'worker', 'database', 'external_dependency', 'orchestration'}:
            raise InvalidPreflightInput('Unsupported/unknown service kind requires explicit review')
        root = s.get('root')
        if kind == 'external_dependency':
            if root != 'external':
                raise InvalidPreflightInput('External dependency root must be external')
        elif safe_path(root) is None:
            raise InvalidPreflightInput('Unsafe service root')
        deps = s.get('depends_on', [])
        if not isinstance(deps, list) or len(deps) > 100 or any(not isinstance(d, str) or not SAFE_NAME.fullmatch(d) for d in deps):
            raise InvalidPreflightInput('Unsafe service dependency')
        p2record = p2.get(name)
        if (p2record is None or p2record.get('kind') != kind or
            p2record.get('source_root') != root or
            p2record.get('depends_on') != deps):
            raise InvalidPreflightInput('Phase 2A service topology does not match Phase 1')
        tech = s.get('technology', '')
        if not isinstance(tech, str) or len(tech) > 256:
            tech = ''
        output.append({'name': name, 'kind': kind, 'root': root, 'deps': list(dict.fromkeys(deps)),
                       'family': _family(tech, kind)})
    if set(p2) != names:
        raise InvalidPreflightInput('Phase 2A contains unrecognized services')
    return output


def _topological_order(services: list[dict[str, Any]]) -> tuple[list[str], list[str]]:
    names = {s['name'] for s in services}
    deps = {s['name']: set(s['deps']) for s in services}
    issues = []
    unknown = sorted({d for dset in deps.values() for d in dset if d not in names})
    if unknown:
        issues.append('UNKNOWN_SERVICE_DEPENDENCIES')
    if any(s['name'] in deps[s['name']] for s in services):
        issues.append('SELF_DEPENDENCY')
    # Lexicographic ordering yields deterministic plans, independent of input order.
    out = []
    pending = {k: v & names for k, v in deps.items()}
    while pending:
        ready = sorted(k for k, v in pending.items() if not v)
        if not ready:
            issues.append('CYCLIC_SERVICE_DEPENDENCIES')
            break
        for n in ready:
            out.append(n)
            del pending[n]
        for v in pending.values():
            v.difference_update(ready)
    return (out if not issues else []), issues


def _is_inside(parent: str, sub: str) -> bool:
    return parent == '.' or sub == parent or sub.startswith(parent.rstrip('/') + '/')


def _build_units(services: list[dict[str, Any]]) -> tuple[list[dict[str, Any]], list[dict[str, Any]], list[str]]:
    units = []
    dependencies = []
    blockers = []
    by_name = {s['name']: s for s in services}
    merged_static = set()
    for s in services:
        if s['kind'] != 'frontend' or s['family'] != 'static_web':
            continue
        for d in s['deps']:
            server = by_name.get(d)
            if (server and server['kind'] == 'backend' and server['family'] == 'php'
                    and _is_inside(server['root'], s['root'])):
                merged_static.add(s['name'])
                break
    for s in services:
        kind, family = s['kind'], s['family']
        if kind == 'database' and family == 'sqlite':
            dependencies.append({
                'name': s['name'], 'kind': 'embedded_database', 'profile': 'sqlite',
                'lifecycle': 'inside_backend_runtime_disposable_file',
                'separate_container_required': False, 'state': 'CANDIDATE_ONLY',
            })
            continue
        if kind == 'external_dependency':
            dependencies.append({
                'name': s['name'], 'kind': 'external_integration', 'profile': family,
                'strategy': 'operator_supplied_offline_mock_or_explicitly_authorized_isolated_resource',
                'network_contact': 'FORBIDDEN_BY_DEFAULT',
                'compatibility_verified': False,
                'reason': ('sdk_endpoint_override_or_emulator_binding_needs_verification'
                           if family == 'aws_s3' else 'external_api_contract_or_mock_needs_verification'),
            })
            blockers.append('UNRESOLVED_EXTERNAL_DEPENDENCY:' + s['name'])
            continue
        if kind == 'frontend' and s['name'] in merged_static:
            dependencies.append({'name': s['name'], 'kind': 'cohosted_ui',
                                 'cohosted_with': next((d for d in s['deps'] if by_name.get(d, {}).get('family') == 'php'), None),
                                 'separate_container_required': False})
            continue
        if kind == 'database' and family in {'mysql', 'mariadb', 'postgresql', 'mongodb', 'redis'}:
            profile = PROFILE[family]
            units.append({
                'name': s['name'], 'kind': 'database_service', 'logical_services': [s['name']],
                'runtime_family': family, 'image_family_hint': profile[0],
                'image_digest': None, 'image_approval': 'REQUIRED_BEFORE_2C',
                'root': None, 'default_port_hint_unverified': profile[2],
                'database_isolation': 'disposable_scoped_volume_only',
                'startup_template': 'BUILTIN_OPERATOR_APPROVED_DATABASE_ADAPTER_ONLY',
            })
            continue
        if family not in PROFILE or family in {'mysql', 'mariadb', 'postgresql', 'mongodb', 'redis'}:
            blockers.append('UNSUPPORTED_RUNTIME_OR_ARCHITECTURE:' + s['name'])
            dependencies.append({'name': s['name'], 'kind': 'manual_adapter_required', 'runtime_family': family})
            continue
        profile = PROFILE[family]
        cohost = sorted(f['name'] for f in services if f['name'] in merged_static and s['name'] in f['deps'])
        units.append({
            'name': s['name'], 'kind': 'application_runtime',
            'logical_services': [s['name']] + cohost,
            'runtime_family': family, 'image_family_hint': profile[0],
            'image_digest': None, 'image_approval': 'REQUIRED_BEFORE_2C',
            'root': s['root'], 'default_port_hint_unverified': profile[2],
            'startup_template': 'OPERATOR_REVIEWED_ADAPTER_REQUIRED',
            'source_scripts_executable': False,
        })
    for u in units:
        # Structured requirements only; these are NOT Docker commands.
        u['container_hardening_candidate'] = {
            'privileged': False, 'read_only_rootfs': True,
            'nonroot_uid': True, 'no_new_privileges': True,
            'drop_all_capabilities': True, 'host_network': False,
            'publish_host_ports': False, 'mount_host_docker_socket': False,
            'allow_external_egress': False,
            'ephemeral_writable_volumes': True,
            'cpu_limit_required': True, 'memory_limit_required': True,
            'pid_limit_required': True, 'timeout_required': True,
            'seccomp_profile_reviewed': False,
            'vm_isolation_verified': False,
        }
    return units, dependencies, blockers


def _environment(manifest: Mapping[str, Any]) -> list[dict[str, Any]]:
    details = manifest.get('inventory') or {}
    details = details if isinstance(details, dict) else {}
    mapping = details.get('environment_variable_details') or {}
    mapping = mapping if isinstance(mapping, dict) else {}
    names = manifest.get('environment_variables') or []
    if not isinstance(names, list):
        names = []
    # Include details keys even if the older top-level list is incomplete.
    all_names = set(x for x in names if isinstance(x, str)) | set(x for x in mapping if isinstance(x, str))
    result = []
    for name in sorted(all_names):
        if not SAFE_ENV.fullmatch(name):
            continue
        d = mapping.get(name) if isinstance(mapping.get(name), dict) else {}
        required_hint = d.get('required_hint') if isinstance(d.get('required_hint'), bool) else None
        default_hint = d.get('default_present') if isinstance(d.get('default_present'), bool) else None
        path = safe_path(d.get('path'), allow_dot=False)
        high_risk = bool(re.search(r'(PASSWORD|SECRET|TOKEN|API_KEY|PRIVATE_KEY|ACCESS_KEY)', name, re.I))
        result.append({
            'name': name, 'required_hint': required_hint, 'default_present_hint': default_hint,
            'declaration_source': path, 'sensitive_name_hint': high_risk,
            'injection_policy': 'generate_synthetic_value_inside_ephemeral_sandbox_if_needed',
            'value': 'NOT_LOADED', 'value_source': 'NOT_LOADED',
            'validation': 'REQUIRED_IN_PHASE_2C',
        })
    return result


def _schema_and_findings(manifest: Mapping[str, Any]) -> tuple[list[dict[str, Any]], list[str], list[str]]:
    schemas = manifest.get('database_schema_files', [])
    schemas = schemas if isinstance(schemas, list) else []
    valid = []
    blockers = []
    for x in schemas[:100]:
        p = safe_path(x, allow_dot=False)
        if not p:
            blockers.append('UNSAFE_DATABASE_SCHEMA_PATH')
        else:
            valid.append({'path': p, 'review_status': 'UNREVIEWED',
                          'initialization': 'DO_NOT_EXECUTE_BEFORE_2C_SQL_REVIEW',
                          'data_target': 'DISPOSABLE_DATABASE_ONLY'})
    findings = manifest.get('findings', [])
    codes = []
    if isinstance(findings, list):
        for finding in findings[:200]:
            if not isinstance(finding, dict):
                continue
            code = finding.get('code')
            if code in {'POSSIBLE_HARDCODED_CREDENTIAL', 'HARDCODED_CREDENTIAL', 'POSSIBLE_SECRET', 'SECRET_IN_SOURCE'}:
                codes.append('POSSIBLE_SOURCE_EMBEDDED_CREDENTIAL')
                blockers.append('DATABASE_OR_SERVICE_CREDENTIAL_REBINDING_REQUIRES_REVIEW')
    return valid, sorted(set(codes)), blockers


def _readiness(manifest: Mapping[str, Any], services: list[dict[str, Any]]) -> list[dict[str, Any]]:
    routes = manifest.get('routes') or []
    pages = manifest.get('pages') or []
    routes = routes if isinstance(routes, list) else []
    pages = pages if isinstance(pages, list) else []
    http = sorted({r.get('path') for r in routes if isinstance(r, dict)
                   and r.get('method') == 'GET'
                   and r.get('kind') in {'declared_http_route', 'file_endpoint', 'file_based_route'}
                   and isinstance(r.get('path'), str)
                   and re.fullmatch(r'/[A-Za-z0-9_/-]*(?:health|healthz|ready|readiness|ping)', r['path'], re.I)})[:12]
    out = []
    for s in services:
        if s['kind'] not in {'frontend', 'backend', 'worker'}:
            continue
        if s['kind'] == 'backend':
            if http:
                probe = {'probe_kind': 'HTTP_GET_CANDIDATE', 'paths': http, 'port': None}
            elif s['family'] == 'php' and pages:
                sample = next((p for p in pages if safe_path(p, allow_dot=False) and p.endswith('.html')), None)
                probe = {'probe_kind': 'HTTP_STATIC_PAGE_CANDIDATE' if sample else 'TCP_OR_HTTP_PROBE_TO_DISCOVER',
                         'path_from_source': ('/' + sample if sample else None), 'port': None}
            else:
                probe = {'probe_kind': 'TCP_OR_HTTP_PROBE_TO_DISCOVER', 'port': None}
        elif s['kind'] == 'frontend':
            probe = {'probe_kind': 'HTTP_ROOT_OR_APP_ROUTE_TO_DISCOVER', 'port': None}
        else:
            probe = {'probe_kind': 'WORKER_HEARTBEAT_OR_PROCESS_CHECK_TO_DISCOVER', 'port': None}
        out.append({'logical_service': s['name'], **probe,
                    'status': 'CANDIDATE_UNTESTED',
                    'validation': 'discover_effective_port_and_startup_timing_inside_2C_sandbox'})
    return out


def create_preflight(manifest: Mapping[str, Any], phase2a_plan: Mapping[str, Any],
                     runtime: Mapping[str, Any] | None = None) -> dict[str, Any]:
    """Deterministic static preparation. No filesystem or process side-effects."""
    if not isinstance(manifest, Mapping) or not isinstance(phase2a_plan, Mapping):
        raise InvalidPreflightInput('Expected JSON objects')
    source = _valid_source(manifest, phase2a_plan)
    services = _check_topology(manifest, phase2a_plan)
    order, topology_issues = _topological_order(services)
    units, dependencies, compatibility_issues = _build_units(services)
    schemas, findings, finding_issues = _schema_and_findings(manifest)
    env = _environment(manifest)
    active_units = {u['name'] for u in units}
    mapped = {name: unit['name'] for unit in units for name in unit['logical_services']}
    network_intents = []
    for s in services:
        for dep in s['deps']:
            if dep not in {t['name'] for t in services}:
                continue
            src_unit = mapped.get(s['name'])
            dst_unit = mapped.get(dep)
            if src_unit and dst_unit and src_unit != dst_unit:
                link = {'from': src_unit, 'to': dst_unit,
                        'policy': 'PRIVATE_INTERNAL_SERVICE_TO_SERVICE_ONLY',
                        'network_verified': False}
                if link not in network_intents:
                    network_intents.append(link)
    engine = dict(runtime) if runtime is not None else unprobed_runtime()
    blockers = sorted(set(topology_issues + compatibility_issues + finding_issues))
    if not services:
        blockers.append('NO_RUNNABLE_SERVICES_IDENTIFIED')
    if schemas:
        # Database schema execution may contain DDL with destructive effects;
        # no SQL content is read or approved by Phase 2B.
        blockers.append('DATABASE_SCHEMA_REVIEW_AND_TEST_IMPORT_PENDING')
    if engine.get('probe_status') == 'PROBED' and engine.get('capability_status') != 'CANDIDATE_REQUIRES_ATTESTATION':
        blockers.append('CONTAINER_ENGINE_REPORTED_POLICY_GAPS')
    if engine.get('probe_status') == 'UNAVAILABLE_OR_UNVERIFIED':
        blockers.append('CONTAINER_ENGINE_NOT_VERIFIED')
    # Explicitly never mark any run as approved, even if runtime info looks good.
    gates = [
        'TRUSTED_DEDICATED_WORKER_VM_NOT_ATTESTED',
        'PINNED_RUNTIME_IMAGES_NOT_APPROVED',
        'NETWORK_DENY_EGRESS_NOT_ENFORCEMENT_TESTED',
        'RESOURCE_LIMITS_AND_TEARDOWN_NOT_FAILURE_TESTED',
        'NO_STUDENT_EXECUTION_AUTHORIZED_BY_THIS_ARTIFACT',
    ]
    if engine.get('probe_status') == 'NOT_REQUESTED':
        gates.append('CONTAINER_ENGINE_NOT_PROBED')
    if any(e['required_hint'] is True for e in env):
        gates.append('SYNTHETIC_REQUIRED_CONFIGURATION_NOT_YET_INJECTED')
    if any(e['required_hint'] is None for e in env):
        gates.append('UNKNOWN_REQUIREDNESS_CONFIGURATION_TO_REVIEW')
    return {
        'schema_version': '0.2', 'phase': 2, 'stage': 'preflight_only',
        'planning_status': 'SUCCESS',
        'status': 'BLOCKED' if blockers else 'PREPARED_NOT_VERIFIED',
        'execution': 'NOT_PERFORMED', 'readiness': 'NOT_VERIFIED',
        'launch_authorization': 'DENIED', 'source': source,
        'phase2a_handoff': {'original_status': phase2a_plan.get('status') if phase2a_plan.get('status') in {'BLOCKED', 'PLAN_ONLY'} else 'UNRECOGNIZED',
                            'source_cross_checked': True, 'topology_cross_checked': True},
        'logical_services': [{'name': s['name'], 'kind': s['kind'], 'family': s['family'],
                              'root': s['root'] if s['kind'] != 'external_dependency' else None,
                              'depends_on': s['deps']} for s in services],
        'provisioning_order_logical': order,
        'candidate_containers': units,
        'container_startup_order_candidate': [x for x in order if x in active_units],
        'interservice_network_intents': sorted(network_intents, key=lambda x: (x['from'], x['to'])),
        'noncontainer_dependencies': dependencies,
        'candidate_container_count': len(units),
        'database_initialization_candidates': schemas,
        'configuration_contract': env,
        'source_safety_findings': findings,
        'readiness_probe_candidates': _readiness(manifest, services),
        'runtime_capability_probe': engine,
        'sandbox_enforcement_requirements': dict(_SANDBOX_BASELINE),
        'blocking_conditions': sorted(set(blockers)),
        'verification_gates': sorted(set(gates)),
        'phase2c_handoff': {
            'policy': 'MUST_VALIDATE_ALL_GATES_IN_EPHEMERAL_DEDICATED_WORKER',
            'image_digest_binding': 'REQUIRED',
            'source_integrity': 'CHECK_SHA_AND_DISALLOW_PATH_ESCAPE_AND_SYMLINK_ESCAPE',
            'startup_command_source': 'CURATED_OPERATOR_REVIEWED_ADAPTER_NOT_README_OR_UNTRUSTED_DOCKERFILE',
            'build_network': 'OFFLINE_OR_APPROVED_MIRROR_ONLY',
            'run_network': 'DENY_EXTERNAL_EGRESS;NO_HOST_PORTS_BY_DEFAULT',
            'database_bootstrap': 'REVIEW_SQL_AND_ONLY_USE_DISPOSABLE_INSTANCES',
            'config': 'CREATE_SYNTHETIC_SECRETS_IN_WORKER;REVIEW_HARDCODED_CREDENTIALS',
            'application_probes': 'PER_SERVICE_DISCOVER_PORTS_AND_TEST_WITH_TIMEOUT',
            'cleanup': 'MANDATORY_ON_SUCCESS_FAILURE_TIMEOUT_AND_CANCELLATION',
            'log_capture': 'MAX_BYTES_REDACTION_AND_NO_SECRET_VALUES',
            'execution': 'NOT_IMPLEMENTED_IN_PHASE_2B',
        },
        'limitations': [
            'Does not download repositories, install dependencies, build images, run services, or test network isolation.',
            'A CLI capability probe is not proof of secure container isolation or resource enforcement.',
            'Native Docker Desktop on a personal Windows workstation is not automatically considered a trusted student-code sandbox.',
            'Static discovery cannot guarantee support for every framework, custom build system, or dynamic application.',
            'No credentials or source-provided executable scripts are copied into this output.',
        ],
    }


def _load_json(path: Path) -> dict[str, Any]:
    p = Path(path)
    if not p.is_file() or p.stat().st_size > MAX_INPUT_BYTES:
        raise InvalidPreflightInput('Input must be an existing JSON file within the 8MB limit')
    try:
        data = json.loads(p.read_text(encoding='utf-8'))
    except (json.JSONDecodeError, UnicodeError) as exc:
        raise InvalidPreflightInput('Input must contain valid UTF-8 JSON') from exc
    if not isinstance(data, dict):
        raise InvalidPreflightInput('Expected top-level JSON object')
    return data


def report_markdown(preflight: Mapping[str, Any]) -> str:
    """Human-readable report intentionally uses only validated/synthesized fields."""
    source = preflight['source']
    items = [
        '# ProjectIQ Executioner — Phase 2B preflight', '',
        '**Execution:** NOT PERFORMED · **Launch authorization:** DENIED', '',
        f"**Source:** `{source['repository']}` @ `{source['commit_sha'] or 'local'}`", '',
        f"**Status:** {preflight['status']}",
        f"**Logical services:** {len(preflight['logical_services'])}",
        f"**Candidate containers:** {preflight['candidate_container_count']}", '',
        '## Proposed application containers', '',
    ]
    for c in preflight['candidate_containers']:
        items.append(f"- `{c['name']}` — {c['runtime_family']} (image family: {c['image_family_hint']}; digest not approved)")
    items.extend(['', '## Dependencies not requiring an application container', ''])
    for d in preflight['noncontainer_dependencies']:
        items.append(f"- `{d['name']}` — {d['kind']} ({d.get('profile', d.get('lifecycle', 'review'))})")
    items.extend(['', '## Blocking conditions', ''])
    items.extend('- ' + b for b in preflight['blocking_conditions'])
    if not preflight['blocking_conditions']:
        items.append('- None identified from this static preparation (runtime remains unverified)')
    items.extend(['', '## Unverified execution gates', ''])
    items.extend('- ' + b for b in preflight['verification_gates'])
    items.extend(['', '## Readiness candidates', ''])
    for p in preflight['readiness_probe_candidates']:
        items.append(f"- `{p['logical_service']}`: {p['probe_kind']} — not attempted")
    items.extend(['', 'No submitted program was run. This report cannot authorize Phase 2C.', ''])
    return '\n'.join(items)


def preflight_from_files(manifest_path: Path, phase2a_path: Path, output_path: Path,
                         *, probe_runtime: bool = False, preferred_runtime: str = 'auto',
                         markdown_path: Path | None = None) -> dict[str, Any]:
    manifest = _load_json(manifest_path)
    plan = _load_json(phase2a_path)
    runtime = probe_container_runtime(preferred_runtime) if probe_runtime else unprobed_runtime()
    result = create_preflight(manifest, plan, runtime)
    dst = Path(output_path)
    dst.parent.mkdir(parents=True, exist_ok=True)
    dst.write_text(json.dumps(result, indent=2, ensure_ascii=False) + '\n', encoding='utf-8')
    if markdown_path is not None:
        md = Path(markdown_path)
        md.parent.mkdir(parents=True, exist_ok=True)
        md.write_text(report_markdown(result), encoding='utf-8')
    return result


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description='Phase 2B: sandbox preflight only; NEVER builds or launches student projects')
    parser.add_argument('--manifest', required=True, type=Path, help='Phase 1 manifest JSON')
    parser.add_argument('--plan', required=True, type=Path, help='Phase 2A plan JSON')
    parser.add_argument('--out', required=True, type=Path, help='Phase 2B proposal JSON')
    parser.add_argument('--md', type=Path, help='Optional readable Markdown report')
    parser.add_argument('--probe-runtime', action='store_true', help='Read-only Docker/Podman info probe (opt in)')
    parser.add_argument('--runtime', choices=['auto', 'docker', 'podman'], default='auto')
    args = parser.parse_args(argv)
    try:
        result = preflight_from_files(args.manifest, args.plan, args.out,
                                      probe_runtime=args.probe_runtime,
                                      preferred_runtime=args.runtime, markdown_path=args.md)
    except (InvalidPreflightInput, InvalidManifest, ValueError, OSError) as exc:
        parser.error(str(exc))
    print('ProjectIQ Executioner — Phase 2B (preflight only, NO EXECUTION)')
    print('  Planning:   ' + result['planning_status'])
    print('  Status:     ' + result['status'])
    print('  Services:   ' + str(len(result['logical_services'])))
    print('  Containers: ' + str(result['candidate_container_count']) + ' candidate(s), none created')
    print('  Blockers:   ' + str(len(result['blocking_conditions'])))
    print('  Runtime:    ' + result['runtime_capability_probe']['probe_status'])
    print('  Output:     ' + str(args.out))
    print('  Execution:  NOT PERFORMED')
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
