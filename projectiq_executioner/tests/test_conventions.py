"""Cross-stack regression corpus. All fixtures are inert strings, never executed."""
from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from projectiq_executioner.analyzer import analyze_directory


def write(root: Path, path: str, source: str) -> None:
    f=root.joinpath(*path.split('/')); f.parent.mkdir(parents=True,exist_ok=True)
    f.write_text(source, encoding='utf-8')


def routes_for(files: dict[str,str]):
    with tempfile.TemporaryDirectory() as d:
        root=Path(d)
        for path,source in files.items(): write(root,path,source)
        return analyze_directory(root)


def route_set(manifest, kind=None):
    return {(r.method,r.path) for r in manifest.routes if kind is None or r.kind==kind}


class JavaScriptServerConventions(unittest.TestCase):
    def test_express_router_nested_mounts_via_esm(self):
        m=routes_for({
            'package.json':'{"dependencies":{"express":"*"}}',
            'server.js':'''import apiRouter from './routes/api.js';
                const app = express(); app.use('/api/v1', apiRouter);''',
            'routes/api.js':'''import userRouter from './users';
                const router = express.Router(); router.use('/users', userRouter);
                export default router;''',
            'routes/users.js':'''const router = express.Router();
                router.get('/', listUsers); router.post('/', addUser);
                router.get('/:id', showUser); export default router;''',
        })
        r=route_set(m,'declared_http_route')
        self.assertIn(('GET','/api/v1/users'),r)
        self.assertIn(('POST','/api/v1/users'),r)
        self.assertIn(('GET','/api/v1/users/:id'),r)
        self.assertNotIn(('GET','/:id'),r)
        self.assertNotIn(('GET','/users/:id'),r)

    def test_commonjs_router_mount(self):
        m=routes_for({'index.js':'''const express=require('express');
            const app=express(); const pRouter=require('./routes/products');
            app.use('/api/products',pRouter);''',
            'routes/products.js':'''const express=require('express');
            const productsRouter=express.Router(); productsRouter.get('/:id',handle);
            module.exports=productsRouter;'''})
        self.assertIn(('GET','/api/products/:id'),route_set(m))

    def test_express_router_route_chain(self):
        m=routes_for({'api.js':'''const router=express.Router();
            router.route('/tasks/:id').get(read).patch(update).delete(remove);'''})
        r=route_set(m)
        self.assertIn(('PATCH','/tasks/:id'),r)
        self.assertIn(('DELETE','/tasks/:id'),r)
        self.assertIn(('GET','/tasks/:id'),r)

    def test_fastify_object_route(self):
        m=routes_for({'server.ts':'''const fastify = require('fastify')();
            fastify.route({method: 'POST', url: '/submit', handler: fn});'''})
        self.assertIn(('POST','/submit'),route_set(m))

    def test_fallback_literal_app_route(self):
        m=routes_for({'server.js':'app.get("/health", handler);'})
        self.assertIn(('GET','/health'),route_set(m))

    def test_unrelated_get_and_dynamic_expression_not_invented(self):
        m=routes_for({'a.js':'''const cache=new Map(); cache.get('/fake');
            const client={get:()=>1}; client.get('/another-fake');
            const router=express.Router(); router.get(`${dynamic}/users`, handler);'''})
        self.assertNotIn(('GET','/fake'),route_set(m))
        self.assertNotIn(('GET','/another-fake'),route_set(m))
        self.assertFalse(any(r.path.endswith('/users') for r in m.routes))

    def test_nestjs_controller_prefix(self):
        m=routes_for({'src/users.controller.ts':'''import {Controller,Get,Post} from '@nestjs/common';
            @Controller('users') export class UsersController {
            @Get(':id') byId() {} @Post() create() {} }'''})
        self.assertIn(('GET','/users/:id'),route_set(m))
        self.assertIn(('POST','/users'),route_set(m))


class ClientHttpConventions(unittest.TestCase):
    def test_axios_client_alias_and_object(self):
        m=routes_for({'src/client.ts':'''import axios from 'axios';
            const client=axios.create({baseURL:'https://example.org'});
            client.get('/tasks'); client.patch('/tasks/1',{});
            axios({method:'DELETE',url:'/tasks/1'});
            client.request({url:'/login',method:'POST'});'''})
        r=route_set(m,'client_http_call')
        for route in [('GET','/tasks'),('PATCH','/tasks/1'),('DELETE','/tasks/1'),('POST','/login')]:
            self.assertIn(route,r)
        self.assertFalse(any('example.org' in r.path for r in m.routes))

    def test_angular_httpclient(self):
        m=routes_for({'src/api.service.ts':'''import {HttpClient} from '@angular/common/http';
            export class Api { constructor(private http:HttpClient) {}
            fetch(){ return this.http.get<User[]>('/api/users'); }
            save(){return this.http.post('/api/users', {name:'X'});} }'''})
        self.assertIn(('GET','/api/users'),route_set(m))
        self.assertIn(('POST','/api/users'),route_set(m))

    def test_ky_and_nuxt_fetch(self):
        m=routes_for({'src/api.ts':'''import ky from 'ky'; const client=ky.create({});
            client.get('/users'); ky.post('/users');
            $fetch('/api/profile',{method:'PUT'});
            useFetch('/api/notifications');'''})
        r=route_set(m,'client_http_call')
        for v in [('GET','/users'),('POST','/users'),('PUT','/api/profile'),('GET','/api/notifications')]: self.assertIn(v,r)

    def test_python_requests_and_httpx(self):
        m=routes_for({'http_client.py':'''import requests as req
            import httpx
            req.post('https://api.example.com/v1/users', json={})
            httpx.get('https://api.example.com/ping')
            fake.get('/not-http')'''})
        self.assertIn(('POST','/v1/users'),route_set(m))
        self.assertIn(('GET','/ping'),route_set(m))
        self.assertFalse(any(r.path=='/not-http' for r in m.routes))


class PythonFrameworkConventions(unittest.TestCase):
    def test_fastapi_apirouter_prefix_and_include(self):
        m=routes_for({'app/api.py':'''from fastapi import APIRouter
            router=APIRouter(prefix='/users')
            app.include_router(router,prefix='/api/v1')
            @router.get('/{user_id}')
            async def get_user(user_id:int): pass
            @router.post('')
            def create(): pass'''})
        r=route_set(m)
        self.assertIn(('GET','/api/v1/users/:user_id'),r)
        self.assertIn(('POST','/api/v1/users'),r)

    def test_flask_blueprint_methods(self):
        m=routes_for({'server.py':'''from flask import Blueprint
            bp=Blueprint('admin',__name__,url_prefix='/admin')
            @bp.route('/users', methods=['GET','POST'])
            def users(): pass'''})
        self.assertIn(('GET','/admin/users'),route_set(m))
        self.assertIn(('POST','/admin/users'),route_set(m))

    def test_django_path_converter(self):
        m=routes_for({'site/urls.py':'''from django.urls import path
            urlpatterns=[path('items/<int:item_id>/', detail), path('',home)]'''})
        self.assertIn(('UNKNOWN','/items/:item_id'),route_set(m))
        self.assertIn(('UNKNOWN','/'),route_set(m))


class OtherFrameworkConventions(unittest.TestCase):
    def test_spring_controller_prefix(self):
        m=routes_for({'src/main/java/ItemsController.java':'''@RestController
            @RequestMapping("/api/items")
            public class ItemsController {
                @GetMapping("/{id}") public Item show() {return null;}
                @PostMapping public Item create() {return null;}
                @DeleteMapping(path="/{id}") public void remove() {}
            }'''})
        self.assertIn(('GET','/api/items/:id'),route_set(m))
        # No-parenthesis @PostMapping is not yet accepted; don't infer it as route.
        self.assertIn(('DELETE','/api/items/:id'),route_set(m))

    def test_aspnet_minimal_api_and_controller(self):
        m=routes_for({'Program.cs':'''var app = builder.Build(); app.MapGet("/health", () => "ok");''',
            'Controllers/UsersController.cs':'''[Route("api/[controller]")]
            public class UsersController : ControllerBase {
                [HttpGet("{id}")] public IActionResult Get(int id) => Ok();
                [HttpPost] public IActionResult Create() => Ok(); }'''})
        self.assertIn(('GET','/health'),route_set(m))
        self.assertIn(('GET','/api/users/:id'),route_set(m))
        self.assertIn(('POST','/api/users'),route_set(m))

    def test_laravel_routes(self):
        m=routes_for({'routes/api.php':'''<?php Route::get('/items', fn()=> []);
            Route::post('/items', [ItemController::class,'store']);
            Route::delete('/items/{id}', fn()=> 1);'''})
        r=route_set(m)
        self.assertIn(('GET','/items'),r)
        self.assertIn(('POST','/items'),r)
        self.assertIn(('DELETE','/items/:id'),r)

    def test_go_gin(self):
        m=routes_for({'main.go':'''import "github.com/gin-gonic/gin"
            r := gin.Default(); r.GET("/health", ready); r.POST("/users/:id",save);'''})
        self.assertIn(('GET','/health'),route_set(m))
        self.assertIn(('POST','/users/:id'),route_set(m))

    def test_ruby_routes(self):
        m=routes_for({'config/routes.rb':'''Rails.application.routes.draw do
            get '/health', to: 'health#index'
            post '/uploads', to: 'upload#create'
            end'''})
        self.assertIn(('GET','/health'),route_set(m))
        self.assertIn(('POST','/uploads'),route_set(m))


class FilesystemRoutingConventions(unittest.TestCase):
    def test_next_dynamic_api_root_and_exported_const(self):
        m=routes_for({'package.json':'{"dependencies":{"next":"*"}}',
            'src/app/api/route.ts':'export async function GET() {}',
            'src/app/api/users/[id]/route.ts':'export const PATCH = async (request)=>{};',
            'src/app/(shop)/items/[id]/page.tsx':'export default function Page(){return null;}'})
        self.assertIn(('GET','/api'),route_set(m))
        self.assertIn(('PATCH','/api/users/:id'),route_set(m))
        self.assertIn(('GET','/items/:id'),route_set(m))
        self.assertNotIn(('PATCH','/api/users/[id]'),route_set(m))

    def test_next_pages_api(self):
        m=routes_for({'src/pages/api/index.ts':'export default function handler(){}',
            'src/pages/api/users/[id].ts':'export default function handler(){}'})
        self.assertIn(('UNKNOWN','/api'),route_set(m))
        self.assertIn(('UNKNOWN','/api/users/:id'),route_set(m))

    def test_sveltekit_pages_and_apis(self):
        m=routes_for({'src/routes/+page.svelte':'<h1>Hello</h1>',
            'src/routes/blog/[slug]/+page.svelte':'<h1>Post</h1>',
            'src/routes/api/items/+server.ts':'export const GET = () => {}; export async function POST(){}'})
        self.assertIn(('GET','/blog/:slug'),route_set(m,'page_route'))
        self.assertIn(('GET','/api/items'),route_set(m,'file_based_route'))
        self.assertIn(('POST','/api/items'),route_set(m,'file_based_route'))

    def test_nuxt_pages_and_server_api(self):
        m=routes_for({'nuxt.config.ts':'export default defineNuxtConfig({});',
            'pages/index.vue':'<template>home</template>',
            'pages/products/[id].vue':'<template>product</template>',
            'server/api/items.get.ts':'export default defineEventHandler(()=>[])',
            'server/api/items/[id].delete.ts':'export default defineEventHandler(()=>{})'})
        self.assertIn(('GET','/'),route_set(m,'page_route'))
        self.assertIn(('GET','/products/:id'),route_set(m,'page_route'))
        self.assertIn(('GET','/api/items'),route_set(m,'file_based_route'))
        self.assertIn(('DELETE','/api/items/:id'),route_set(m,'file_based_route'))

    def test_react_router_literal(self):
        m=routes_for({'src/routes.tsx':'''import {Route,createBrowserRouter} from 'react-router-dom';
            const router=createBrowserRouter([{path:'/shop', element:<Shop/>}]);
            const layout=<Route path="/cart" element={<Cart/>}/>'''})
        self.assertIn(('GET','/shop'),route_set(m,'page_route'))
        self.assertIn(('GET','/cart'),route_set(m,'page_route'))

    def test_vue_angular_routes(self):
        m=routes_for({'src/router.ts':'''import {createRouter, createWebHistory} from 'vue-router';
            const routes = [{path:'/home',component: Home}];''',
            'src/app.routes.ts':'''import {Routes} from '@angular/router';
            export const routes: Routes = [{ path: 'users', component: Users }];'''})
        self.assertIn(('GET','/home'),route_set(m,'page_route'))
        self.assertIn(('GET','/users'),route_set(m,'page_route'))


class SafetyInvariantTests(unittest.TestCase):
    def test_reject_undefined_expressions_no_unjustified_http_methods(self):
        m=routes_for({'server.js':'''const app=express();
            app.get(base + '/unsafe',handler); app.post(`/${x + y}`,handler);
            const client=new Storage();client.get('/not-an-http-call');'''})
        self.assertFalse(any('/unsafe' in r.path or '/not-an-http-call' in r.path for r in m.routes))

    def test_evidence_line_provenance(self):
        m=routes_for({'app.py':'\n\nfrom fastapi import FastAPI\napp=FastAPI()\n@app.get("/ping")\ndef ping(): return 1\n'})
        hits=[r for r in m.routes if r.path=='/ping']
        self.assertEqual(hits[0].evidence[0].line,5)

    def test_dont_execute_or_copy_credential_text(self):
        m=routes_for({'src/api.ts':'''import axios from 'axios';
            const client=axios.create({baseURL:'https://example.net'});
            client.get('/safe'); const DB_PASSWORD = 'secret_password_123';'''})
        serialized=json.dumps(m.to_dict())
        self.assertNotIn('secret_password_123',serialized)
        self.assertNotIn('https://example.net',serialized)



class MultiFileAndFileRoleTests(unittest.TestCase):
    def test_fastapi_cross_module_prefix(self):
        m=routes_for({'app/main.py':"""from .api import users
            from fastapi import FastAPI
            app=FastAPI()
            app.include_router(users.router, prefix='/api/v2')""",
            'app/api/users.py':"""from fastapi import APIRouter
            router = APIRouter(prefix='/users')
            @router.get('/{id}')
            def one(id:int):pass"""})
        self.assertIn(('GET','/api/v2/users/:id'),route_set(m))
        self.assertNotIn(('GET','/users/:id'),route_set(m))
        hit=next(r for r in m.routes if r.path=='/api/v2/users/:id')
        self.assertEqual({e.path for e in hit.evidence},{'app/main.py','app/api/users.py'})

    def test_fastapi_from_module_alias(self):
        m=routes_for({'main.py':"""from routers.users import router as users_router
            app.include_router(users_router, prefix='/v1')""",
            'routers/users.py':"""from fastapi import APIRouter
            router=APIRouter()
            @router.post('/users')
            def make():pass"""})
        self.assertIn(('POST','/v1/users'),route_set(m))

    def test_imported_shared_axios_instance(self):
        m=routes_for({'src/http.ts':"""import axios from 'axios';
            const http = axios.create({baseURL:'https://api.test.com'});
            export default http;""",
            'src/products.ts':"""import api from './http';
            const load=()=>api.get('/products'); const update=()=>api.put('/products/7',{})"""})
        self.assertIn(('GET','/products'),route_set(m,'client_http_call'))
        self.assertIn(('PUT','/products/7'),route_set(m,'client_http_call'))

    def test_file_role_catalog_and_report(self):
        from projectiq_executioner.report import render_markdown
        m=routes_for({'src/pages/cart/page.tsx':'export default function(){}',
            'src/components/Button.tsx':'export default function(){}',
            'src/lib/api.ts':"fetch('/api/users');",
            'src/models/user.ts':'export interface User {id: string}',
            'db/migrations/001.sql':'CREATE TABLE users (id INT);',
            'tests/user.test.ts':"test('a', ()=>{});",
            'public/logo.svg':'<svg/>',
            '.github/workflows/ci.yml':'name: ci',
            'package.json':'{"name":"x"}',
            '.env.example':'TOKEN=',
            'README.md':'# Demo'})
        roles=m.inventory['file_roles']
        self.assertIn('ui_component',roles['src/components/Button.tsx'])
        self.assertIn('http_client',roles['src/lib/api.ts'])
        self.assertIn('model_or_type',roles['src/models/user.ts'])
        self.assertIn('database_schema_or_migration',roles['db/migrations/001.sql'])
        self.assertIn('test',roles['tests/user.test.ts'])
        self.assertIn('ci_workflow',roles['.github/workflows/ci.yml'])
        self.assertIn('configuration',roles['.env.example'])
        self.assertIn('### File role inventory',render_markdown(m))

    def test_non_source_file_remains_catalogued(self):
        m=routes_for({'data/random.bin':'not source','config/random.yml':'foo: bar'})
        self.assertIn('data/random.bin',m.inventory['file_roles'])
        self.assertEqual(sum('other_file' in x for x in m.inventory['file_roles'].values()),2)

class MachineReadableContractTests(unittest.TestCase):
    def test_openapi_json_paths(self):
        m=routes_for({'openapi.json':json.dumps({'openapi':'3.1.0','paths':{
            '/v1/users/{user_id}':{'get':{'responses':{}},'patch':{'responses':{}}},
            '/health':{'get':{'responses':{}}}
        }})})
        r=route_set(m,'declared_api_contract')
        self.assertIn(('GET','/v1/users/:user_id'),r)
        self.assertIn(('PATCH','/v1/users/:user_id'),r)
        self.assertIn(('GET','/health'),r)
        self.assertTrue(any('OpenAPI/Swagger contract'==t.name for t in m.technologies))

    def test_openapi_yaml_paths(self):
        m=routes_for({'openapi.yaml':'''openapi: 3.0.0
info:
  title: Demo
paths:
  /users/{id}:
    get:
      summary: List
    delete:
      summary: Delete
  /orders:
    post:
      summary: Create
components:
  schemas:
    User:
      type: object
'''})
        r=route_set(m,'declared_api_contract')
        self.assertIn(('GET','/users/:id'),r)
        self.assertIn(('DELETE','/users/:id'),r)
        self.assertIn(('POST','/orders'),r)

    def test_bad_openapi_doesnt_crash(self):
        m=routes_for({'swagger.json':'{broken_json'})
        self.assertTrue(any(f.code=='OPENAPI_PARSE_FAILED' for f in m.findings))

    def test_grpc_rpc_methods_are_not_http(self):
        m=routes_for({'proto/user.proto':'''syntax = "proto3";
package demo.v1;
service Users {
  rpc FindUser (UserRequest) returns (UserResponse);
  rpc ListUsers (Empty) returns (UserList);
}'''})
        self.assertIn(('RPC','demo.v1.Users.FindUser'),route_set(m,'rpc_method'))
        self.assertFalse(any('demo.v1.Users.FindUser' in r.path and r.kind=='declared_http_route' for r in m.routes))

    def test_graphql_schema_does_not_invent_endpoint(self):
        m=routes_for({'schema.graphql':'''type Query {
  users: [User!]!
  user(id: ID!): User
}
type Mutation {
  createUser(input: UserInput!): User
}'''})
        r=route_set(m,'graphql_operation')
        self.assertIn(('QUERY','Query.users'),r)
        self.assertIn(('MUTATION','Mutation.createUser'),r)
        self.assertFalse(any(item.path=='/graphql' for item in m.routes))

class ExtendedCoverageRegressionTests(unittest.TestCase):
    def test_laravel_resource_and_no_fake_php_endpoint(self):
        m=routes_for({'routes/api.php':"""<?php
            Route::apiResource('products', ProductController::class);
            Route::get('/status', fn()=>true);"""})
        got=route_set(m,'declared_http_route')
        self.assertIn(('GET','/products'),got)
        self.assertIn(('POST','/products'),got)
        self.assertIn(('DELETE','/products/:id'),got)
        self.assertFalse(any(r.path=='routes/api.php' and r.kind=='file_endpoint' for r in m.routes))

    def test_rust_actix_axum(self):
        m=routes_for({'src/main.rs':'''use actix_web::get;
            #[get("/health")]
            async fn health() {}
            let app=Router::new().route("/users", post(create));
            // axum::Router'''} )
        self.assertIn(('GET','/health'),route_set(m))
        self.assertIn(('POST','/users'),route_set(m))

    def test_go_standard_library(self):
        m=routes_for({'main.go':'''import "net/http"
            http.HandleFunc("GET /health", health)
            http.HandleFunc("/ping",ping)'''})
        self.assertIn(('GET','/health'),route_set(m))
        self.assertIn(('UNKNOWN','/ping'),route_set(m))

    def test_python_session_calls(self):
        m=routes_for({'api.py':'''import requests
            session=requests.Session()
            session.post("https://example.org/users")'''})
        self.assertIn(('POST','/users'),route_set(m,'client_http_call'))

    def test_named_axios_export_alias_and_no_false_positive(self):
        m=routes_for({'src/client.ts':'''import axios from 'axios';
            export const api = axios.create({});
            export const util = {get: x=>x};''',
            'src/one.ts':'''import {api as client} from './client';
            client.get('/yes');''',
            'src/two.ts':'''import {util} from './client';
            util.get('/not-http');'''})
        self.assertIn(('GET','/yes'),route_set(m,'client_http_call'))
        self.assertFalse(any(r.path=='/not-http' for r in m.routes))

    def test_graphql_is_api_contract_not_database_schema(self):
        m=routes_for({'schema.graphql':'type Query {\n user(id: ID!): User\n}\n'})
        self.assertNotIn('schema.graphql',m.database_schema_files)
        self.assertIn('api_contract',m.inventory['file_roles']['schema.graphql'])

class UnresolvedPatternTests(unittest.TestCase):
    def test_dynamic_fetch_is_reported_not_guessed(self):
        m=routes_for({'src/http.ts':'''const url=computeEndpoint();
            const result=fetch(url);'''})
        self.assertEqual(route_set(m,'client_http_call'),set())
        self.assertTrue(any(x['kind']=='dynamic_fetch' for x in m.inventory['unresolved_api_sites']))
        self.assertTrue(any('dynamic API expressions' in x for x in m.unknowns))

    def test_python_external_api_label_without_host(self):
        m=routes_for({'client.py':'''import requests
            requests.get('https://private-api.example.com/health')'''})
        self.assertIn(('GET','/health'),route_set(m,'client_http_call'))
        self.assertTrue(any(s.kind=='external_dependency' for s in m.services))
        self.assertNotIn('private-api.example.com',json.dumps(m.to_dict()))

if __name__ == "__main__":
    unittest.main()

class HtmlFormConventionsTests(unittest.TestCase):
    def test_html_form_action_method_order_independent(self):
        m=routes_for({'public/login.html':'''<form method="POST" id="login" action="/api/login"><input /></form>
            <form action='/search'><input /></form>'''})
        self.assertIn(('POST','/api/login'),route_set(m,'form_action'))
        self.assertIn(('GET','/search'),route_set(m,'form_action'))

    def test_unknown_relative_form_action_not_fabricated(self):
        m=routes_for({'public/login.html':'''<form method="POST" action="../api/login.php"></form>'''})
        self.assertFalse(any(r.kind=='form_action' for r in m.routes))

class CloudServiceAndWrapperConventions(unittest.TestCase):
    """Patterns characteristic of many Vite/FastAPI monorepos, not one repository."""

    def test_nested_wrapper_defaults_and_local_api_prefix(self):
        m=routes_for({
            'frontend/package.json':'{"dependencies":{"react":"^19","vite":"^6"}}',
            'frontend/src/App.jsx': '''
                const TOKEN_KEY = 'session_token';
                const API = '/api';
                async function request(path, options = {}, token = localStorage.getItem(TOKEN_KEY)) {
                    const headers = { ...(options.headers || {}) };
                    const response = await fetch(`${API}${path}`, { ...options, headers });
                    return response.json();
                }
                async function save(id) {
                    await request('/files');
                    await request('/files/presign', {method: 'POST'});
                    await request(`/files/${id}/complete`, {method: 'POST'});
                    await request(`/files/${id}`, {method: 'DELETE'});
                    await fetch(presign.upload_url, {method: 'PUT'});
                }
            ''',
            'backend/requirements.txt':'fastapi==0.1',
            'backend/app/main.py': '''from fastapi import FastAPI
app=FastAPI()
@app.get('/api/files')
def files(): pass
@app.post('/api/files/presign')
def presign(): pass
'''
        })
        clients=route_set(m, 'client_http_call')
        self.assertIn(('GET','/api/files'),clients)
        self.assertIn(('POST','/api/files/presign'),clients)
        self.assertIn(('POST','/api/files/:param/complete'),clients)
        self.assertIn(('DELETE','/api/files/:param'),clients)
        self.assertNotIn(('PUT','/api/files'),clients)
        frontend=next(x for x in m.services if x.kind=='frontend')
        backend=next(x for x in m.services if x.kind=='backend')
        self.assertEqual(frontend.name, 'frontend')
        self.assertEqual(backend.name, 'backend')
        self.assertIn(backend.name,frontend.depends_on)

    def test_sqlite_s3_sdk_and_dependencies_from_proven_usage(self):
        m=routes_for({
            'backend/requirements.txt':'fastapi==0.1\nSQLAlchemy==2.0\nboto3==1.37\nPyJWT==2.10',
            'backend/app/config.py':'''from pydantic_settings import BaseSettings
class Settings(BaseSettings):
    database_url: str = "sqlite:///./project.db"
''',
            'backend/app/services/storage.py':'''import boto3
s3 = boto3.client("s3", region_name="ap-south-1")
''',
            'backend/app/routes/items.py':'from fastapi import APIRouter\nrouter=APIRouter()\n@router.get("/items")\ndef items(): pass',
        })
        names={t.name for t in m.technologies}
        for n in ('FastAPI','SQLite','Amazon S3','boto3 (AWS SDK)','PyJWT'):
            self.assertIn(n,names)
        self.assertIn('sqlite',{s.name for s in m.services})
        self.assertIn('amazon_s3',{s.name for s in m.services})
        backend=next(s for s in m.services if s.kind=='backend')
        self.assertIn('sqlite',backend.depends_on)
        self.assertIn('amazon_s3',backend.depends_on)
        roles=m.inventory['file_roles']
        self.assertIn('configuration',roles['backend/app/config.py'])
        self.assertIn('service_logic',roles['backend/app/services/storage.py'])

    def test_file_roles_distinguish_models_config_bootstrap_and_url_service_dir(self):
        m=routes_for({
            'package.json':'{"dependencies":{"next":"16.0.0","react":"19.0.0"}}',
            'src/app/services/corporate/page.tsx':'export default function Page(){return null}',
            'src/app/page.tsx':'export default function Page(){return null}',
            'backend/app/models.py':'class User: pass',
            'backend/app/schemas.py':'class UserSchema: pass',
            'backend/app/database.py':'connection = None',
            'backend/app/security.py':'def verify(): pass',
            'backend/app/config.py':'settings = None',
            'backend/app/routes/__init__.py':'',
            'deployment/nginx.conf':'server {}',
            'deployment/clouddrop.service':'[Service]\nExecStart=/usr/bin/true',
            '.gitignore':'*.pyc',
            'frontend/src/App.jsx':'export default function App(){return null}',
            'frontend/src/main.jsx':'import App from "./App"',
            'frontend/vite.config.js':'export default {}',
        })
        roles=m.inventory['file_roles']
        self.assertIn('ui_page',roles['src/app/services/corporate/page.tsx'])
        self.assertNotIn('service_logic',roles['src/app/services/corporate/page.tsx'])
        for f in ('backend/app/models.py','backend/app/schemas.py'):
            self.assertIn('model_or_type',roles[f])
        self.assertIn('configuration',roles['backend/app/config.py'])
        self.assertIn('configuration',roles['.gitignore'])
        self.assertIn('database_access',roles['backend/app/database.py'])
        self.assertIn('authentication_logic',roles['backend/app/security.py'])
        self.assertIn('module_initializer',roles['backend/app/routes/__init__.py'])
        self.assertNotIn('api_or_route',roles['backend/app/routes/__init__.py'])
        self.assertIn('deployment_config',roles['deployment/nginx.conf'])
        self.assertIn('deployment_config',roles['deployment/clouddrop.service'])
        self.assertIn('ui_app_entrypoint',roles['frontend/src/App.jsx'])
        self.assertIn('ui_app_entrypoint',roles['frontend/src/main.jsx'])
        self.assertIn('configuration',roles['frontend/vite.config.js'])
        self.assertNotIn('other_source',roles['frontend/vite.config.js'])

    def test_non_s3_boto3_client_does_not_invent_s3(self):
        m=routes_for({'requirements.txt':'boto3==1.2.3', 'worker.py':'''import boto3
client=boto3.client('dynamodb')
'''})
        self.assertIn('boto3 (AWS SDK)',{t.name for t in m.technologies})
        self.assertNotIn('Amazon S3',{t.name for t in m.technologies})
        self.assertFalse(any(s.name=='amazon_s3' for s in m.services))

    def test_generic_non_network_request_function_not_an_http_client(self):
        m=routes_for({'src/api.js':'''function request(path, options={}) { return cache[path]; }
request('/not-an-api', {method:'DELETE'});'''})
        self.assertNotIn(('DELETE','/not-an-api'),route_set(m,'client_http_call'))

    def test_literal_ternary_client_urls_both_reachable(self):
        m=routes_for({'src/auth.jsx':'''const API='/api';
            async function request(path, options={}, token=localStorage.getItem('token')) {
              return fetch(`${API}${path}`, options);
            }
            request(`/auth/${authMode === 'login' ? 'login' : 'register'}`, {method:'POST'});
            const suffix = versionId ? `?version_id=${encodeURIComponent(versionId)}` : '';
            request(`/files/${file.id}/download${suffix}`);
        '''})
        clients=route_set(m,'client_http_call')
        self.assertIn(('POST','/api/auth/login'),clients)
        self.assertIn(('POST','/api/auth/register'),clients)
        self.assertIn(('GET','/api/files/:param/download'),clients)
        self.assertFalse(any('download:param' in path for _meth,path in clients))
        self.assertFalse(any('auth/:param' in path for _meth,path in clients))
