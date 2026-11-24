"""Bounded local Ollama lifecycle management, not an available-RAM guarantee."""
from __future__ import annotations
import time
from urllib.parse import urlparse


def _canonical(name: str) -> str:
    return name if ':' in name.rsplit('/', 1)[-1] else name + ':latest'


def release_ollama_model(client, *, attempts=5, pause=0.5):
    """Unload only this collector's configured model, then check /api/ps.

    Intended for a dedicated local Ollama instance. Never delete model files,
    stop the server, or stop other models. Return bounded diagnostic codes.
    """
    try:
        base = client.base.rstrip('/')
        parsed = urlparse(base)
        model = client.s.model
        if (parsed.scheme not in ('http', 'https') or parsed.hostname not in ('localhost', '127.0.0.1', '::1')
                or parsed.username or parsed.password or parsed.path or parsed.query or parsed.fragment
                or not isinstance(model, str) or not model.strip()):
            return 'RELEASE_NOT_VERIFIED'
        wanted = _canonical(model)
        def loaded():
            response = client.http.get(base + '/api/ps', timeout=5, allow_redirects=False)
            if response.status_code != 200:
                raise ValueError('PS_FAILED')
            rows = response.json()['models']
            if not isinstance(rows, list):
                raise ValueError('PS_INVALID')
            names = []
            for row in rows:
                if not isinstance(row, dict):
                    raise ValueError('PS_INVALID')
                name = row.get('name') or row.get('model')
                if not isinstance(name, str) or not name:
                    raise ValueError('PS_INVALID')
                names.append(_canonical(name))
            return wanted in names
        if not loaded():
            return 'ALREADY_UNLOADED'
        response = client.http.post(base + '/api/generate',
            json={'model':model, 'keep_alive':0, 'stream':False},
            timeout=10, allow_redirects=False)
        if response.status_code != 200:
            return 'RELEASE_NOT_VERIFIED'
        for i in range(attempts):
            if not loaded():
                return 'UNLOAD_VERIFIED'
            if i + 1 < attempts:
                time.sleep(pause)
    except Exception:
        # A resource diagnostic must not erase the model's evaluation result.
        pass
    return 'RELEASE_NOT_VERIFIED'
