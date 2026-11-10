import { spawn } from 'node:child_process';
import { mkdtemp, rm } from 'node:fs/promises';
import { tmpdir } from 'node:os';
import { join, resolve } from 'node:path';

let playwright;
try {
  playwright = await import('playwright');
} catch {
  console.error('SKIP: Playwright chưa có trong môi trường; không tự cài dependency/browser.');
  process.exitCode = 77;
}

if (playwright) {
  const root = resolve(new URL('../..', import.meta.url).pathname);
  const wardrobe = await mkdtemp(join(tmpdir(), 'shadow-vton-e2e-'));
  const port = 8765;
  const server = spawn(resolve(root, '.venv/bin/python'), ['-m', 'uvicorn', 'main:app', '--host', '127.0.0.1', '--port', String(port)], {
    cwd: resolve(root, 'backend'),
    env: { ...process.env, SHADOW_MODE: 'mock', SHADOW_WARDROBE_DIR: wardrobe, SHADOW_RATE_LIMIT: 'off', SHADOW_ACCESS_CODE: '', DECART_API_KEY: '', TRYON_PHOTO_PROVIDER: 'mock' },
    stdio: ['ignore', 'pipe', 'pipe'],
  });
  let serverLog = '';
  server.stderr.on('data', (chunk) => { serverLog += chunk.toString(); });
  const baseURL = `http://127.0.0.1:${port}`;
  try {
    for (let attempt = 0; attempt < 50; attempt += 1) {
      try { if ((await fetch(`${baseURL}/api/tryon/config`)).ok) break; } catch { /* starting */ }
      await new Promise((resolveWait) => setTimeout(resolveWait, 100));
      if (attempt === 49) throw new Error(`Server không khởi động:\n${serverLog}`);
    }
    const { run } = await import('./tryon-mock.spec.mjs');
    await run({ chromium: playwright.chromium, baseURL });
    console.log('PASS: VTON browser mock E2E');
  } finally {
    server.kill('SIGTERM');
    await rm(wardrobe, { recursive: true, force: true });
  }
}
