"""Thin adapters to existing Phase 4 reviewed executors.

No student-supplied command lines, shell execution or improvised oracles.
Preflight calls the SAME compiler as the executor, BEFORE VM launch.
Each run is self-contained in its own disposable VM(s), not a persistent
session shared with other untrusted projects.
"""
from __future__ import annotations

from importlib import import_module
from pathlib import Path
from typing import Any

from .contracts import (OrchestrationError, SUITE_CATALOGS, read_json, reject,
                        safe_output_path, no_symlinks)

# Modules/functions are fixed allowlisted code, not taken from request data.
_REGISTRY = {
    'readiness': ('phase4.handoff','prepare_handoff','phase4.vm_run','run_reviewed_tests'),
    'api': ('phase4.reviewed_api','compile_reviewed_api_bundle','phase4.api_vm_run','run_reviewed_api'),
    'auth': ('phase4.reviewed_auth','compile_reviewed_auth_bundle','phase4.auth_vm_run','run_reviewed_auth'),
    'db_fixture': ('phase4.db_fixture_contracts','compile_reviewed_db_fixture','phase4.db_fixture_vm_run','run_reviewed_db_fixture'),
    'db_postconditions': ('phase4.db_postcondition_contracts','compile_reviewed_postconditions','phase4.db_postcondition_vm_run','run'),
    'scenario_isolation': ('phase4.scenario_isolation_contracts','compile_reviewed_isolation','phase4.scenario_isolation_vm_run','run'),
    'dependent_workflow': ('phase4.dependent_workflow_contracts','compile_reviewed_dependent_workflow','phase4.dependent_workflow_vm_run','run'),
    'crud': ('phase4.crud_workflow_contracts','compile_reviewed_crud','phase4.crud_workflow_vm_run','run'),
    'browser_ui': ('phase4.browser_ui_contracts','compile_reviewed_browser','phase4.browser_ui_vm_run','run'),
    'browser_login': ('phase4.browser_login_contracts','compile_reviewed_login','phase4.browser_login_vm_run','run'),
}

# Inventory of output artifacts emitted by existing Phase 4 runners.
_RUN_FILES = {
    'readiness': ('phase4_run.json','vm_execution.json'),
    'api': ('api_run.json','api_vm_execution.json'),
    'auth': ('auth_run.json','auth_vm_execution.json'),
    'db_fixture': ('db_fixture_run.json','db_fixture_vm_execution.json'),
    'db_postconditions': ('d3_run.json','d3_vm_execution.json'),
    'scenario_isolation': ('d4_run.json',None),
    'dependent_workflow': ('d4a_run.json','d4a_vm_execution.json'),
    'crud': ('d4b_run.json','d4b_vm_execution.json'),
    'browser_ui': ('d4c_run.json','d4c_vm_execution.json'),
    'browser_login': ('d4d_run.json','d4d_vm_execution.json'),
}


def output_files(kind: str) -> tuple[str,str|None]:
    return _RUN_FILES[kind]


def load_catalogs(spec: dict, review_root: Path) -> dict[str, dict]:
    root = Path(review_root).resolve(strict=True)
    if not root.is_dir() or not no_symlinks(root):
        reject('UNSAFE_OPERATOR_REVIEW_ROOT')
    out = {}
    for name, rel in spec['catalogs'].items():
        if not safe_output_path(rel):
            reject('UNSAFE_CATALOG_PATH')
        path = root.joinpath(*rel.split('/'))
        if not no_symlinks(path) or not path.is_relative_to(root):
            reject('UNSAFE_CATALOG_PATH')
        out[name] = read_json(path,limit=256_000)
    return out


def _args(kind: str, plan: dict, candidate: dict, config: dict, catalogs: dict,
          *, output_dir: Path|None = None, work_root: Path|None = None):
    # Existing runners differ in naming conventions. Never use introspective
    # arbitrary argument spreading from untrusted request JSON.
    if kind == 'readiness':
        return {'plan':plan,'candidate':candidate,'config':config}
    if kind == 'api':
        return {'plan':plan,'catalog':catalogs['oracle'],'candidate':candidate,'config':config}
    if kind == 'auth':
        return {'plan':plan,'auth_catalog':catalogs['auth'], 'oracle_catalog':catalogs['oracle'],
                'candidate':candidate,'config':config}
    if kind == 'db_fixture':
        return {'plan':plan,'manifest':catalogs['fixture'], 'candidate':candidate,'config':config}
    if kind == 'db_postconditions':
        return {'plan':plan,'auth':catalogs['auth'],'oracles':catalogs['oracle'],
                'postconditions':catalogs['postconditions'],'candidate':candidate,'config':config}
    if kind == 'scenario_isolation':
        return {'plan':plan,'auth':catalogs['auth'],'oracles':catalogs['oracle'],
                'postconditions':catalogs['postconditions'],'manifest':catalogs['isolation'],
                'candidate':candidate,'config':config}
    if kind == 'dependent_workflow':
        return {'plan':plan,'fixture_catalog':catalogs['fixture'],
                'workflow_catalog':catalogs['workflow'],'candidate':candidate,'config':config}
    if kind == 'crud':
        return {'plan':plan,'auth_catalog':catalogs['auth'],'oracle_catalog':catalogs['oracle'],
                'crud_catalog':catalogs['crud'],'candidate':candidate,'config':config}
    if kind == 'browser_ui':
        return {'plan':plan,'catalog':catalogs['browser'],'candidate':candidate,'config':config}
    if kind == 'browser_login':
        return {'plan':plan,'catalog':catalogs['login'],'candidate':candidate,'config':config}
    reject('UNKNOWN_REVIEWED_RUNNER')


def preflight(kind: str, *, plan: dict, candidate: dict, config: dict,
              catalogs: dict) -> dict:
    path, fn, _, _ = _REGISTRY[kind]
    compiler = getattr(import_module('projectiq_executioner.'+path),fn)
    args = _args(kind,plan,candidate,config,catalogs)
    if kind == 'readiness':
        report = compiler(plan,candidate,config)
        if report.get('status') != 'STAGED_NOT_EXECUTED':
            return {'approved':False,'code':report.get('status','HANDOFF_NOT_READY'),
                    'selected':report.get('summary',{}).get('selected',0)}
        return {'approved':True,'code':'APPROVED','selected':report['summary']['selected']}
    if kind in ('api','auth','db_fixture','db_postconditions','scenario_isolation',
                'dependent_workflow','crud','browser_ui','browser_login'):
        # All compilers accept these arguments in this order EXCEPT browser login,
        # which takes (plan, candidate, catalog, config).
        if kind == 'scenario_isolation':
            compiler(plan,catalogs['auth'],catalogs['oracle'],catalogs['postconditions'],
                     candidate,config,catalogs['isolation'])
        elif kind == 'browser_login':
            compiler(plan,candidate,catalogs['login'],config)
        elif kind == 'readiness':
            pass
        elif kind == 'api':
            compiler(plan,catalogs['oracle'],candidate,config)
        elif kind == 'auth':
            compiler(plan,catalogs['auth'],catalogs['oracle'],candidate,config)
        elif kind == 'db_fixture':
            compiler(plan,catalogs['fixture'],candidate,config)
        elif kind == 'db_postconditions':
            compiler(plan,catalogs['auth'],catalogs['oracle'],catalogs['postconditions'],candidate,config)
        elif kind == 'dependent_workflow':
            compiler(plan,catalogs['fixture'],catalogs['workflow'],candidate,config)
        elif kind == 'crud':
            compiler(plan,catalogs['auth'],catalogs['oracle'],catalogs['crud'],candidate,config)
        elif kind == 'browser_ui':
            compiler(plan,catalogs['browser'],candidate,config)
        return {'approved':True,'code':'APPROVED','selected':None}
    reject('UNKNOWN_REVIEWED_RUNNER')


def invoke(kind: str, *, plan: dict, candidate: dict, config: dict, catalogs: dict,
           output_dir: Path, work_root: Path|None = None) -> dict:
    _, _, path, func = _REGISTRY[kind]
    runner = getattr(import_module('projectiq_executioner.'+path),func)
    args = _args(kind,plan,candidate,config,catalogs)
    args['output_dir']=output_dir
    if work_root is not None:
        args['work_root']=work_root
    return runner(**args)
