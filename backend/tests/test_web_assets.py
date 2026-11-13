import hashlib
import json

from fastapi import FastAPI
from fastapi.testclient import TestClient
from starlette.middleware.gzip import GZipMiddleware

from web_assets import WebFiles


def test_compact_bundle_is_compressed_and_source_edits_are_never_hidden_by_stale_build(tmp_path):
    (tmp_path / '.build').mkdir()
    source = 'export const source = "new";'
    (tmp_path / 'app.js').write_text(source)
    compiled = '/* compact */' + 'const x=1;' * 200
    (tmp_path / '.build' / 'app.js').write_text(compiled)
    (tmp_path / '.build' / 'manifest.json').write_text(json.dumps({'app.js': {'app.js': hashlib.sha256(source.encode()).hexdigest()}}))
    app = FastAPI()
    app.mount('/', GZipMiddleware(WebFiles(directory=tmp_path), minimum_size=500))
    with TestClient(app) as client:
        response = client.get('/app.js', headers={'Accept-Encoding': 'gzip'})
        assert response.text == compiled
        assert response.headers['content-encoding'] == 'gzip'
        assert response.headers['content-type'].startswith('text/javascript')
        (tmp_path / 'app.js').write_text('export const source = "edited";')
        assert client.get('/app.js').text == 'export const source = "edited";'


def test_source_only_frontend_keeps_all_screen_styles_in_one_request(tmp_path):
    (tmp_path / 'styles.css').write_text('.base{}')
    (tmp_path / 'conversation.css').write_text('.camera{}')
    app = FastAPI()
    app.mount('/', WebFiles(directory=tmp_path))
    with TestClient(app) as client:
        response = client.get('/styles.css')
        assert response.status_code == 200
        assert '.base{}' in response.text and '.camera{}' in response.text
