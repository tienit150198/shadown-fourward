"""Serve a compact frontend when built; keep source-only development working."""
import hashlib
import json
from pathlib import Path

from fastapi.responses import FileResponse, Response
from fastapi.staticfiles import StaticFiles

CSS_FILES = ('styles.css', 'wardrobe.css', 'tryon-live.css', 'conversation.css')


class WebFiles(StaticFiles):
    def compiled(self, name: str) -> Path | None:
        root = Path(self.directory)
        candidate = root / '.build' / name
        try:
            manifest = json.loads((root / '.build' / 'manifest.json').read_text())
            inputs = manifest[name]
            if not candidate.is_file() or not inputs:
                return None
            for source, expected in inputs.items():
                file = (root / source).resolve()
                if not file.is_relative_to(root.resolve()) or hashlib.sha256(file.read_bytes()).hexdigest() != expected:
                    return None
        except (OSError, ValueError, KeyError, TypeError):
            return None
        return candidate

    async def get_response(self, path: str, scope):
        if path in ('app.js', 'styles.css'):
            compiled = self.compiled(path)
            if compiled:
                return FileResponse(compiled, media_type='text/javascript' if path.endswith('.js') else 'text/css', headers={'Cache-Control': 'no-cache'})
            if path == 'styles.css':
                root = Path(self.directory)
                content = '\n'.join((root / name).read_text() for name in CSS_FILES if (root / name).is_file())
                return Response(content, media_type='text/css', headers={'Cache-Control': 'no-cache'})
        return await super().get_response(path, scope)
