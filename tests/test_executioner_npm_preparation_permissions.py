"""Source-order regression for the reviewed single-service npm preparation path.

Checks the actual guest function without starting a VM or executing guest code.
Live Linux permissions were separately exercised by the reviewed Vite pilot.
"""
import ast
from pathlib import Path


def test_single_service_npm_runtime_shared_before_app_launch():
    source=Path(__file__).resolve().parents[1]/'projectiq_executioner/phase2/vm_worker/guest_agent.py'
    tree=ast.parse(source.read_text(encoding='utf-8-sig'))
    fn=next(n for n in tree.body if isinstance(n,ast.FunctionDef) and n.name=='prepare_npm_cache')
    def named_calls(node,name):
        return [n for n in ast.walk(node) if isinstance(n,ast.Call) and isinstance(n.func,ast.Name) and n.func.id==name]
    extract=[(i,c) for i,st in enumerate(fn.body) for c in named_calls(st,'_node_tar_extract')]
    shares=[(i,st.value) for i,st in enumerate(fn.body) if isinstance(st,ast.Expr) and isinstance(st.value,ast.Call) and isinstance(st.value.func,ast.Name) and st.value.func.id=='_share_tree' and len(st.value.args)==1 and isinstance(st.value.args[0],ast.Name) and st.value.args[0].id=='runtime']
    launches=[(i,c) for i,st in enumerate(fn.body) for c in named_calls(st,'_trusted_process') if any(k.arg=='project_code' and isinstance(k.value,ast.Constant) and k.value.value is True for k in c.keywords)]
    assert len(extract)==1 and len(shares)==1 and launches
    share_index,share=shares[0]
    assert extract[0][0]<share_index<min(i for i,_ in launches)
    assert len(share.keywords)==1 and share.keywords[0].arg=='writable' and isinstance(share.keywords[0].value,ast.Constant) and share.keywords[0].value.value is False
