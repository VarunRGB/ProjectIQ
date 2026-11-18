"""Phase 2C VM-only contracts. All content derived from student manifests is data.

No Docker or Podman daemon is required or consulted by this module.
"""
from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any, Mapping

from ..preflight import InvalidPreflightInput, _valid_source, _check_topology, safe_path

_SHA = re.compile(r'^[0-9a-f]{40}$')


class VMPlanError(ValueError):
    pass


def load_json(path: Path, limit: int = 8_000_000) -> dict:
    file = Path(path)
    if not file.is_file() or file.stat().st_size > limit:
        raise VMPlanError('Input JSON not found or exceeds size limit')
    try:
        obj = json.loads(file.read_text('utf-8'))
    except (ValueError, UnicodeError) as exc:
        raise VMPlanError('Invalid JSON input') from exc
    if not isinstance(obj, dict):
        raise VMPlanError('Expected JSON object')
    return obj


def build_vm_plan(manifest: Mapping[str, Any], phase2a: Mapping[str, Any],
                  phase2b: Mapping[str, Any]) -> dict:
    """Compile a *non-authorizing* native-Linux-VM plan.

    Legacy Docker/Podman probe results from Phase 2B do not gate this VM path.
    Phase 2B's architecture and source consistency checks are revalidated.
    """
    try:
        src = _valid_source(manifest, phase2a)
        services = _check_topology(manifest, phase2a)
    except InvalidPreflightInput as exc:
        raise VMPlanError(str(exc)) from exc
    if src['kind'] != 'github' or not _SHA.fullmatch((src['commit_sha'] or '').lower()):
        raise VMPlanError('VM pilot requires a pinned GitHub SHA')
    if (phase2b.get('phase') != 2 or phase2b.get('stage') != 'preflight_only'
            or phase2b.get('execution') != 'NOT_PERFORMED'
            or phase2b.get('planning_status') != 'SUCCESS'):
        raise VMPlanError('Expected successful, unexecuted Phase 2B preflight')
    p2src = phase2b.get('source') or {}
    if not isinstance(p2src, Mapping) or any(p2src.get(k) != src.get(k) for k in ('kind', 'repository', 'commit_sha')):
        raise VMPlanError('Phase 2B source mismatch')
    # Reject tampered topology, including a changed dependency list or family.
    p2services = phase2b.get('logical_services')
    if not isinstance(p2services, list) or len(p2services) != len(services):
        raise VMPlanError('Phase 2B topology mismatch')
    s1 = {x['name']: x for x in services}
    s2 = {x.get('name'): x for x in p2services if isinstance(x, dict)}
    if set(s1) != set(s2):
        raise VMPlanError('Phase 2B service names differ')
    for name, val in s1.items():
        second = s2[name]
        if (second.get('kind') != val['kind'] or second.get('root') !=
            (None if val['kind'] == 'external_dependency' else val['root']) or
            second.get('family') != val['family'] or second.get('depends_on') != val['deps']):
            raise VMPlanError('Phase 2B topology record changed')

    backends = [s for s in services if s['kind'] == 'backend']
    frontends = [s for s in services if s['kind'] == 'frontend']
    dbs = [s for s in services if s['kind'] == 'database']
    others = [s for s in services if s['kind'] in ('worker','orchestration','external_dependency')]
    adapter = None
    expected_probe = None
    manual_review = []
    schema = None
    if (not backends and len(frontends) == 1 and frontends[0]['family'] == 'static_web'
            and not dbs and not others):
        pages = manifest.get('pages') or []
        if isinstance(pages, list) and 'index.html' in pages:
            adapter = 'static_html_v1'
            expected_probe = '/index.html'
        else:
            manual_review.append('STATIC_SITE_NEEDS_EXPLICIT_INDEX_ENTRYPOINT')
    elif (len(backends) == 1 and backends[0]['family'] == 'php'
          and len(frontends) <= 1 and all(f['family'] == 'static_web' for f in frontends)
          and len(dbs) == 1 and dbs[0]['family'] in ('mysql', 'mariadb') and not others
          and backends[0]['root'] == '.'):
        # Generic PHP-file-endpoint + SQL dump prototype; more stacks use separate adapters.
        pages = manifest.get('pages') or []
        schemas = manifest.get('database_schema_files') or []
        if isinstance(pages, list) and isinstance(schemas, list):
            pages = [p for p in pages if safe_path(p, allow_dot=False) and p.lower().endswith('.html')]
            schemas = [p for p in schemas if safe_path(p, allow_dot=False) and p.lower().endswith('.sql')]
            if pages and len(schemas) == 1:
                adapter = 'php_mysql_v1'
                expected_probe = '/' + ('public/login.html' if 'public/login.html' in pages else sorted(pages)[0])
                schema = schemas[0]
                manual_review.extend(['SQL_DUMP_APPLY_ONLY_INSIDE_THROWAWAY_VM', 'PHP_PDO_CONNECTOR_REBIND_IN_VM_ONLY'])
            else:
                manual_review.append('PHP_MYSQL_NEEDS_HTML_ENTRY_AND_SINGLE_SQL_SCHEMA')
    if adapter is None:
        manual_review.append('NO_CURATED_VM_GUEST_ADAPTER_FOR_DETECTED_ARCHITECTURE')

    candidate = {
        'schema_version': '0.3', 'phase': 2, 'stage': 'vm_execution_candidate',
        'planning_status': 'SUCCESS',
        'status': 'CANDIDATE_NEEDS_VM_VERIFICATION' if adapter else 'NEEDS_ADAPTER',
        'launch_authorization': 'DENIED_UNTIL_OPERATOR_APPROVAL_AND_RUNTIME_GATES',
        'execution': 'NOT_PERFORMED',
        'source': {'kind': 'github', 'repository': src['repository'], 'commit_sha': src['commit_sha'].lower()},
        'worker_provider': 'qemu_qcow2_v1',
        'worker_lifecycle': 'fresh_immutable_base_plus_disposable_copy_on_write_overlay',
        'runtime_dependency': 'qemu_system_and_qemu_img; NO_DOCKER_OR_PODMAN',
        'adapter': adapter,
        'probe_path': expected_probe,
        'schema_path': schema,
        'guest_profile': 'ubuntu_template_prebaked_offline',
        'linux_guest_mode': 'single_vm_multiple_local_processes',
        'application_preparation': {
            'potentially_automatable': adapter is not None,
            'review_items': manual_review,
            'previous_2b_blockers': phase2b.get('blocking_conditions', []),
            'docker_probe_ignored_on_vm_path': True,
        },
        'vm_limits': {
            'vcpu': 2, 'ram_mib': 3072 if adapter == 'php_mysql_v1' else 1024,
            'wall_seconds': 240 if adapter == 'php_mysql_v1' else 140,
            'max_snapshot_zip_bytes': 30_000_000,
            'max_output_bytes': 64_000,
        },
        'required_security_gates': [
            'APPROVED_TEMPLATE_SHA256_MATCH', 'KNOWN_QEMU_ACCELERATOR',
            'COW_OVERLAY_PER_RUN', 'NO_HOST_FILESYSTEM_MOUNTS', 'GUEST_EGRESS_RESTRICTED',
            'SSH_CONTROL_BOUND_LOOPBACK_ONLY', 'EPHEMERAL_SSH_KEYS_AND_INSTANCE_ID',
            'SOURCE_EXTRACTED_IN_GUEST_WITH_SAFE_PATH_CHECKS',
            'GUEST_AGENT_PREINSTALLED_RUNTIME_AND_CURATED_COMMANDS',
            'TIMEOUT_BOUNDED_TEARDOWN', 'OPERATOR_REVIEWED_REPOSITORY_SHA',
        ],
        'limitations': [
            'VM image installation/baking and security acceptance remain operator tasks.',
            'Only two guest adapters are implemented; other architectures are not claimed runnable.',
            'No Docker daemon is required; Phase 2B Docker probe statuses are legacy data only.',
            'Successful HTTP readiness proves startup only, not application correctness.',
        ],
    }
    return candidate
