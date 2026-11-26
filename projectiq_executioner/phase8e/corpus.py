"""Pinned public corpus and deterministic local controls. Synthetic locks are plan-only."""
from __future__ import annotations
import base64, hashlib, json
from pathlib import Path

# Immutable external commit identities were checked against GitHub. Expectations
# are hypotheses, not execution approvals or guarantees of compatibility.
PUBLIC = (
    ('public-static','mdn/beginner-html-site-scripted','7e7e6527e10072ff0cacda499145ffba63af057e','STATIC_HTTP',(), 'Independent HTML/CSS/browser-JS site'),
    ('public-svelte','render-examples/sveltekit','b2a44d02ec53243010562551b9298fa54e0b6eea','NODE_SVELTEKIT',(), 'npm-locked SvelteKit adapter-node example'),
    ('public-streamlit','streamlit/streamlit-example','8bd2197e4ba68dd68127a264dc6708f0a96f23c8','PY_STREAMLIT',(), 'Ranged/unpinned data-science dependencies; wheel budget may block'),
    ('public-express-yarn','render-examples/express-hello-world','039c34770852fb07cef7f9f0f8534c5de408b207','NODE_HTTP',('NPM_LOCK_REQUIRED','NPM_CONFIG_OR_OTHER_MANAGER_REQUIRES_REVIEW'), 'Yarn-only lock; no implicit conversion'),
    ('public-next-yarn','render-examples/nextjs-hello-world','88cac038955a3a14a7a69b6664c789f799c1d6db','NODE_NEXT',('NPM_LOCK_REQUIRED','NPM_CONFIG_OR_OTHER_MANAGER_REQUIRES_REVIEW'), 'Yarn-only Next application'),
    ('public-fastapi-extra','render-examples/fastapi','f829276c68503f7afae195c3e3f778f085242cb0','PY_ASGI',('REQUIREMENT_UNSUPPORTED',), 'fastapi[all] requires unsupported extras'),
    ('public-django-db','render-examples/django','fbc60fcb44eebd63cfd45566e6973183e6f75aa4','PY_DJANGO',('EXTERNAL_OR_DATABASE_SERVICE_UNRESOLVED','ENVIRONMENT_CONFIGURATION_UNRESOLVED'), 'PostgreSQL and deployment configuration'),
)

def node(deps, files, **extra):
    # Intentionally fake digests: never exported as runtime fixtures or downloaded.
    from ..phase8c2.broker import exact_url
    pkg={'name':'piq-plan-only','version':'1.0.0','dependencies':{n:'1.0.0' for n in deps},**extra}
    rows={'':{k:v for k,v in pkg.items() if k in ('name','version','dependencies','devDependencies','optionalDependencies','peerDependencies')}}
    for n in deps:
        rows['node_modules/'+n]={'version':'1.0.0','resolved':exact_url(n,'1.0.0'),'integrity':'sha512-'+base64.b64encode(hashlib.sha512(('synthetic-not-an-archive:'+n).encode()).digest()).decode()}
    return {'package.json':json.dumps(pkg),'package-lock.json':json.dumps({'name':pkg['name'],'version':'1.0.0','lockfileVersion':3,'packages':rows}),**files}

# Actual operator-created smoke applications; no fake lock data or remote URLs.
# Exported only to a new directory; never uploaded or approved automatically.
SMOKE = {
 'static':('STATIC_HTTP',{'index.html':'<!doctype html><html><body><h1>PIQ static smoke</h1></body></html>\n'}),
 'php':('PHP_HTTP',{'index.php':'<?php header("Content-Type: text/plain"); echo "PIQ PHP smoke";\n'}),
 'node-http':('NODE_HTTP',{'server.js':'const http=require("node:http");\nhttp.createServer((req,res)=>{res.end("PIQ Node smoke");}).listen(8765,"127.0.0.1");\n'}),
 'node-cli':('NODE_CLI',{'package.json':'{"name":"piq-cli","version":"1.0.0","main":"index.js"}\n','index.js':'if (2 + 2 !== 4) process.exit(1);\nconsole.log("PIQ Node CLI");\n'}),
 'python-cli':('PY_CLI',{'main.py':'if __name__ == "__main__":\n    assert sum([1,2,3]) == 6\n    print("PIQ Python CLI")\n'}),
 'asgi':('PY_ASGI',{'service.py':'from fastapi import FastAPI as F\napi=F()\n@api.get("/")\ndef home():\n    return {"status":"ok"}\n','pyproject.toml':'[project]\nname="piq-asgi-smoke"\nversion="1.0.0"\nrequires-python=">=3.12,<3.13"\ndependencies=["fastapi>=0.115,<1", "uvicorn>=0.30,<1"]\n'}),
 'flask-inferred':('PY_FLASK',{'server.py':'from flask import Flask as F\nweb=F(__name__)\n@web.get("/")\ndef home():\n    return "PIQ inferred smoke"\n'}),
 'django':('PY_DJANGO',{'requirements.txt':'Django>=5.1,<6\n','manage.py':'import os,sys\nfrom django.core.management import execute_from_command_line\nif __name__ == "__main__":\n    os.environ.setdefault("DJANGO_SETTINGS_MODULE","settings")\n    execute_from_command_line(sys.argv)\n','settings.py':'SECRET_KEY="local-test-only-not-a-production-secret"\nDEBUG=False\nALLOWED_HOSTS=["127.0.0.1","localhost"]\nROOT_URLCONF="urls"\nINSTALLED_APPS=[]\nMIDDLEWARE=[]\nDATABASES={"default":{"ENGINE":"django.db.backends.sqlite3","NAME":":memory:"}}\n','urls.py':'from django.urls import path\nfrom django.http import HttpResponse\ndef home(request):\n    return HttpResponse("PIQ Django smoke")\nurlpatterns=[path("",home)]\n'}),
 'streamlit':('PY_STREAMLIT',{'requirements.txt':'streamlit>=1.40,<2\n','dashboard.py':'import streamlit as st\nst.title("PIQ Streamlit smoke")\n'}),
 'c':('NATIVE_C',{'main.c':'int main(void) { return 0; }\n'}),
 'cpp':('NATIVE_CPP',{'main.cpp':'int main() { return 0; }\n'}),
 'java':('JAVA_SINGLE',{'Main.java':'class Main { public static void main(String[] args) { System.out.println("PIQ Java"); } }\n'}),
 'go':('GO_SINGLE',{'main.go':'package main\nfunc main() {}\n'}),
 'rust':('RUST_SINGLE',{'main.rs':'fn main() { println!("PIQ Rust"); }\n'}),
}

def controls():
    rows=[(k,a,files,(),k=='flask-inferred') for k,(a,files) in SMOKE.items()]
    for k,a,deps,files in (
        ('vite','NODE_VITE',['vite','react'],{'index.html':'<html>ok</html>'}),
        ('next','NODE_NEXT',['next','react','react-dom'],{'app/page.js':'export default function Page(){return null}'}),
        ('cra','NODE_CRA',['react-scripts','react'],{'src/index.js':'console.log(1)'}),
        ('astro','NODE_ASTRO',['astro'],{'src/pages/index.astro':'<h1>ok</h1>'}),
        ('nuxt','NODE_NUXT',['nuxt'],{'app.vue':'<template>ok</template>'}),
        ('svelte','NODE_SVELTEKIT',['@sveltejs/kit','@sveltejs/adapter-node','vite'],{'src/routes/+page.svelte':'<h1>ok</h1>'}),
        ('angular','NODE_ANGULAR',['@angular/core','@angular/cli'],{'angular.json':json.dumps({'projects':{'app':{'projectType':'application'}}})}),
        ('express','NODE_HTTP',['express'],{'server.js':'require("express")().listen(8765)'}),
        ('fastify','NODE_HTTP',['fastify'],{'server.js':'require("fastify")().listen({port:8765})'}),
        ('koa','NODE_HTTP',['koa'],{'server.js':'new (require("koa"))().listen(8765)'}),
        ('hono','NODE_HTTP',['hono'],{'server.js':'const {Hono}=require("hono");const app=new Hono();app.get("/",c=>c.text("ok"));'}),
    ): rows.append((k,a,node(deps,files),(),False))
    rows.extend([
        ('deny-npm-unlocked','NODE_NEXT',{'package.json':'{"dependencies":{"next":"^16"}}','app/page.js':'export default function Page(){return null}'},('NPM_LOCK_REQUIRED',),False),
        ('deny-yarn','NODE_HTTP',{'package.json':'{"dependencies":{"express":"^5"}}','yarn.lock':'# yarn lock','app.js':'require("express")().listen(8765)'},('NPM_LOCK_REQUIRED',),False),
        ('deny-extra','PY_ASGI',{'main.py':'from fastapi import FastAPI\napp=FastAPI()','requirements.txt':'fastapi[all]'},('REQUIREMENT_UNSUPPORTED',),False),
        ('deny-inference','PY_FLASK',SMOKE['flask-inferred'][1],('INFERRED_DEPENDENCIES_REQUIRE_OPERATOR_OPT_IN',),False),
        ('deny-dynamic','PY_CLI',{'main.py':'import importlib\nif __name__ == "__main__": importlib.import_module("missing")'},('UNKNOWN_OR_DYNAMIC_IMPORTS',),True),
        ('deny-env','NODE_HTTP',{'server.js':'const h=require("http");const key=process.env.SECRET_TOKEN;h.createServer(()=>{}).listen(8765)'},('ENVIRONMENT_CONFIGURATION_UNRESOLVED',),False),
        ('deny-docker','NODE_HTTP',{'server.js':SMOKE['node-http'][1]['server.js'],'Dockerfile':'FROM node:20'},('DEPLOYMENT_OR_SUBMODULE_POLICY_UNRESOLVED',),False),
        ('deny-go-module','GO_SINGLE',{'main.go':'package main\nfunc main() {}','go.mod':'module test.example/demo\n\ngo 1.23\n'},('BUILD_SYSTEM_OR_MODULE_RESOLVER_NOT_IMPLEMENTED',),False),
        ('deny-ambiguous','PY_FLASK',{'app.py':'from flask import Flask\na=Flask(__name__)\nb=Flask(__name__)','requirements.txt':'Flask'},('PYTHON_ENTRYPOINT_AMBIGUOUS',),False),
        ('deny-node-version','NODE_HTTP',{'package.json':'{"engines":{"node":"^18"}}','server.js':SMOKE['node-http'][1]['server.js']},('PROVISIONED_NODE_VERSION_INCOMPATIBLE_OR_UNRESOLVED',),False),
    ])
    return rows

def write_tree(root, files):
    root=Path(root);root.mkdir(parents=True,exist_ok=False)
    for name,body in sorted(files.items()):
        p=root.joinpath(*name.split('/'));p.parent.mkdir(parents=True,exist_ok=True);p.write_bytes(body.encode('utf-8'))

def export_files():
    return {f'{key}/{name}':body for key,(_,files) in SMOKE.items() for name,body in files.items()}
