"""D5c Gate 4: validate actual reviewed-fixture runtime registry evidence.

No VM is launched by this validator. The pinned three-project regression harness
must be run AFTER applying Gate 4; this validates those real-VM reports and the
managed-run root. Does not attest arbitrary untrusted student execution.
"""
from __future__ import annotations

import argparse
import json
import os
from pathlib import Path

from .d5c_orphan_registry import ID_PATTERN, ROOT_KEY, ROOT_LEASE, _root
from .d5c_runtime_workspace import ManagedRuntimeRun, RuntimeWorkspaceError
from .orchestrator import load_config

ATTESTATION = 'NOT_ATTESTED_FOR_UNTRUSTED_STUDENTS'
SUCCESS = 'D5C_GATE4_REAL_RUNTIME_INTEGRATED_NOT_ATTESTED'
CLEAN = 'VM_STOPPED_DISPOSABLE_STORAGE_REMOVED'
EXPECTED = {'FSDL': (1, 'NOT_APPLICABLE', 'static_html_v1'),
            'MiniCollabApp': (1, 'NOT_APPLICABLE', 'php_mysql_v1'),
            'FullStack': (2, 'PREEXEC_KERNEL_HANDOFF_VERIFIED', 'node_fullstack_offline_v1')}
CHECKS = ('reviewed_fixture_reports_bound_to_summary', 'all_three_startups_http_200',
          'real_runtime_signed_lease_registration', 'distinct_managed_run_ids',
          'actual_vm_storage_cleanup_verified', 'no_leftover_managed_runs',
          'fullstack_build_claim_not_promoted', 'security_attestation_disabled')


def _read_json(path: Path, limit: int) -> dict:
    path = Path(path)
    info = path.lstat()
    if hasattr(info, 'st_file_attributes') and info.st_file_attributes & 0x400:
        raise ValueError('REPARSE_REPORT')
    if path.is_symlink() or not path.is_file() or not 0 < info.st_size <= limit:
        raise ValueError('INVALID_REPORT_FILE')
    record = json.loads(path.read_text('utf-8-sig'))
    if type(record) is not dict:
        raise ValueError('INVALID_REPORT_OBJECT')
    return record


def validate(summary: Path, config_path: Path) -> dict:
    report = {'schema_version':'0.1', 'stage':'d5c_gate4_runtime_integration',
              'status':'NOT_VERIFIED', 'security_attestation':ATTESTATION,
              'student_code_executed_by_validator':False,
              'checks':{name:'NOT_VERIFIED' for name in CHECKS}}
    try:
        summary = Path(summary).absolute()
        body = _read_json(summary, 100_000)
        if body.get('Passed') is not True or body.get('SecurityAttestation') != ATTESTATION:
            raise ValueError('REGRESSION_SUMMARY_NOT_PASSED')
        rows = body.get('Results')
        if not isinstance(rows,list) or len(rows)!=3:
            raise ValueError('INVALID_FIXTURE_ROWS')
        config=load_config(config_path)
        root=ManagedRuntimeRun(config).root
        _root(root)
        ids=[]
        all_http=True
        all_proven=True
        all_clean=True
        all_claims=True
        for row in rows:
            if type(row) is not dict or row.get('Fixture') not in EXPECTED:
                raise ValueError('UNKNOWN_FIXTURE')
            name=row['Fixture']
            expected_file=summary.parent / name.lower() / 'vm_execution.json'
            if Path(str(row.get('Report',''))).absolute() != expected_file.absolute():
                raise ValueError('FIXTURE_REPORT_PATH_MISMATCH')
            one=_read_json(expected_file,200_000)
            expected_servers,expected_build,expected_adapter=EXPECTED[name]
            g=one.get('guest_result',{})
            placement=g.get('cgroup_placement',{}) if type(g) is dict else {}
            build=g.get('build_launch_evidence',{}) if type(g) is dict else {}
            source=one.get('source')
            if (type(source) is not dict or type(source.get('repository')) is not str or
                    type(source.get('commit_sha')) is not str or
                    source['repository']+'@'+source['commit_sha'] not in config['approved_snapshots']):
                raise ValueError('REPORT_SOURCE_NOT_APPROVED')
            if one.get('adapter')!=expected_adapter or one.get('backend')!='QEMU_DISPOSABLE_QCOW2_VM':
                raise ValueError('REPORT_ADAPTER_OR_BACKEND_MISMATCH')
            if (row.get('Startup')!='PASS' or str(row.get('HTTP'))!='200' or
                    row.get('Cleanup')!='PASS'):
                raise ValueError('REGRESSION_ROW_NOT_VERIFIED')
            all_http &= (one.get('status')=='STARTUP_VERIFIED' and
                         one.get('execution')=='PERFORMED_IN_DISPOSABLE_VM' and
                         type(g) is dict and g.get('http_status')==200 and
                         type(placement) is dict and placement.get('server_count')==expected_servers and
                         placement.get('status')=='SERVER_PIDS_VERIFIED')
            all_proven &= one.get('managed_runtime')=='SIGNED_LEASED_WORKSPACE_REMOVED'
            all_clean &= one.get('cleanup')==CLEAN
            all_claims &= (one.get('security_attestation')==ATTESTATION and
                           type(build) is dict and build.get('status')==expected_build and
                           type(placement) is dict and placement.get('builds_verified') is False and
                           placement.get('database_service_verified') is False)
            ident=one.get('managed_run_id')
            if type(ident) is not str or not ID_PATTERN.fullmatch(ident):
                raise ValueError('INVALID_MANAGED_RUN_ID')
            ids.append(ident)
        if {x['Fixture'] for x in rows} != set(EXPECTED):
            raise ValueError('MISSING_REQUIRED_FIXTURE')
        report['checks']['reviewed_fixture_reports_bound_to_summary']='PASS'
        if all_http: report['checks']['all_three_startups_http_200']='PASS'
        if all_proven: report['checks']['real_runtime_signed_lease_registration']='PASS'
        if len(set(ids))==3: report['checks']['distinct_managed_run_ids']='PASS'
        if all_clean: report['checks']['actual_vm_storage_cleanup_verified']='PASS'
        # Gate 5 adds a permanent root lease; existing Gate 4 roots may still
        # have only the root key until their first Gate 5 runtime operation.
        if {x.name for x in root.iterdir()} in ({ROOT_KEY}, {ROOT_KEY, ROOT_LEASE}):
            report['checks']['no_leftover_managed_runs']='PASS'
        if all_claims:
            report['checks']['fullstack_build_claim_not_promoted']='PASS'
            report['checks']['security_attestation_disabled']='PASS'
    except (OSError, ValueError, RuntimeWorkspaceError, KeyError, TypeError,
            UnicodeError, json.JSONDecodeError) as exc:
        report['failure_code']=type(exc).__name__
    if all(x=='PASS' for x in report['checks'].values()):
        report['status']=SUCCESS
    return report


def main(argv: list[str] | None=None) -> int:
    parser=argparse.ArgumentParser(description='D5c Gate 4: reviewed fixture runtime integration')
    parser.add_argument('--regression-summary', type=Path, required=True)
    parser.add_argument('--vm-config', type=Path, required=True)
    parser.add_argument('--out', type=Path, required=True)
    args=parser.parse_args(argv)
    result=validate(args.regression_summary,args.vm_config)
    args.out.parent.mkdir(parents=True,exist_ok=True)
    args.out.write_text(json.dumps(result,sort_keys=True,indent=2)+'\n',encoding='utf-8')
    print('ProjectIQ D5c Gate 4 - '+result['status'])
    print('  Report:',args.out)
    return 0 if result['status']==SUCCESS else 1

if __name__=='__main__':
    raise SystemExit(main())
