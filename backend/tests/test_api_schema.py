from fastapi.testclient import TestClient

from main import app


def test_mobile_schema_auth_and_stream_transports():
    schema = app.openapi()
    paths = schema['paths']
    assert paths['/api/health']['get']['security'] == []
    assert paths['/api/access']['post']['security'] == []
    wardrobe = paths['/api/wardrobe/items']['get']
    assert wardrobe['security'] == [{'ShadowAccessHeader': []}, {'ShadowAccessCookie': []}]
    assert wardrobe['responses']['200']['content']['application/json']['schema']['items']['$ref'].endswith('/WardrobeItem')
    assert 'Retry-After' in wardrobe['responses']['429']['headers']
    assert '401' in wardrobe['responses']
    assert '413' in paths['/api/tryon/photo']['post']['responses']
    assert 'application/json' not in paths['/api/voice/speech']['post']['responses']['200']['content']
    assert 'audio/wav' in paths['/api/voice/speech']['post']['responses']['200']['content']
    assert 'audio/mp4' in paths['/api/voice/transcribe']['post']['requestBody']['content']
    assert paths['/wardrobe-media/{rel_path}']['get']['security']


def test_openapi_ref_targets_resolve():
    schema = app.openapi()
    def walk(value):
        if isinstance(value, dict):
            if '$ref' in value and value['$ref'].startswith('#/'):
                target = schema
                for part in value['$ref'][2:].split('/'):
                    target = target[part]
            for item in value.values():
                walk(item)
        elif isinstance(value, list):
            for item in value:
                walk(item)
    walk(schema)


def test_interactive_documentation_is_available():
    with TestClient(app) as client:
        swagger = client.get('/docs')
        assert swagger.status_code == 200 and 'SwaggerUIBundle' in swagger.text
        assert '"persistAuthorization": false' in swagger.text
        assert client.get('/redoc').status_code == 200
        spec = client.get('/openapi.json')
        assert spec.status_code == 200
        assert 'ShadowAccessHeader' in spec.json()['components']['securitySchemes']
