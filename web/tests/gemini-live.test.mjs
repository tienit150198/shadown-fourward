import assert from 'node:assert/strict';
import { readFile } from 'node:fs/promises';
import test from 'node:test';

const resamplerSource = await readFile(new URL('../js/pcm-resampler.js', import.meta.url), 'utf8');
const { PcmResampler } = await import(`data:text/javascript;base64,${Buffer.from(resamplerSource).toString('base64')}`);
const source = (await readFile(new URL('../js/gemini-live.js', import.meta.url), 'utf8'))
  .replace(/^import .*from '\.\/api.js';\n/m, 'const fetchWithAccess = globalThis.__fetchWithAccess;\n')
  .replaceAll('import.meta.url', JSON.stringify(new URL('../js/gemini-live.js', import.meta.url).href));
const { startGeminiLive, pcmFromBase64, bytesToBase64 } = await import(`data:text/javascript;base64,${Buffer.from(source).toString('base64')}`);

test('PCM resampling has exact duration and is invariant to arbitrary render boundaries', () => {
  for (const rate of [44100, 48000]) {
    const signal = Float32Array.from({ length: rate }, (_, i) => 0.7 * Math.sin(i / 25));
    const expected = new PcmResampler(rate).process(signal);
    const streaming = new PcmResampler(rate);
    const blocks = [];
    for (let offset = 0; offset < signal.length; offset += 127) blocks.push(...streaming.process(signal.subarray(offset, offset + 127)));
    assert.equal(blocks.length, 16000);
    assert.deepEqual(Int16Array.from(blocks), expected);
  }
});

test('PCM conversion preserves little endian sign and rejects malformed frames', () => {
  const bytes = Uint8Array.of(0, 128, 0, 0, 255, 127);
  assert.deepEqual([...pcmFromBase64(bytesToBase64(bytes))], [-1, 0, 32767 / 32768]);
  assert.throws(() => pcmFromBase64('AA=='), /PCM/);
  assert.throws(() => pcmFromBase64('!'), /Invalid/);
  assert.deepEqual([...new PcmResampler(16000).process(Float32Array.of(-2, 2))], [-32768, 32767]);
});

function browserHarness() {
  const tracks = [{ stopped: false, stop() { this.stopped = true; } }];
  const stream = { getTracks: () => tracks };
  const nodes = [], contexts = [], captures = [], sockets = [];
  class Node { connect() {} disconnect() { this.disconnected = true; } }
  class Context {
    currentTime = 0; destination = {}; state = 'running';
    audioWorklet = { addModule: async () => {} };
    constructor() { contexts.push(this); }
    async resume() {}
    async close() { this.state = 'closed'; }
    createMediaStreamSource() { return new Node(); }
    createGain() { const node = new Node(); node.gain = {}; return node; }
    createBuffer(_, length, rate) { return { duration: length / rate, copyToChannel() {} }; }
    createBufferSource() { const node = new Node(); node.start = () => {}; node.stop = () => { node.stopped = true; }; nodes.push(node); return node; }
  }
  class Capture extends Node { constructor() { super(); this.port = {}; captures.push(this); } }
  class Socket {
    static OPEN = 1; static CLOSING = 2;
    readyState = 1; bufferedAmount = 0; sent = [];
    constructor(url) { this.url = String(url); sockets.push(this); queueMicrotask(() => this.onopen()); }
    send(raw) { const item = JSON.parse(raw); this.sent.push(item); if (item.type === 'start') queueMicrotask(() => this.emit({ type: 'ready', max_session_seconds: 180 })); }
    emit(message) { this.onmessage({ data: JSON.stringify(message) }); }
    close() { this.readyState = 3; this.onclose?.(); }
  }
  globalThis.AudioContext = Context;
  globalThis.AudioWorkletNode = Capture;
  globalThis.WebSocket = Socket;
  Object.defineProperty(globalThis, 'navigator', { configurable: true, value: { mediaDevices: { getUserMedia: async () => stream } } });
  globalThis.location = { href: 'https://example.test/#/conversation', protocol: 'https:' };
  return { stream, tracks, nodes, contexts, captures, sockets };
}

test('relay uses app socket, sends PCM only after ready, cancels audio and releases all resources', async () => {
  const browser = browserHarness();
  const events = [], closed = [];
  const session = await startGeminiLive({ configuredCity: 'Hà Nội', history: [], onEvent: item => events.push(item), onClose: reason => closed.push(reason) });
  const socket = browser.sockets[0];
  assert.equal(socket.url, 'wss://example.test/session/voice');
  assert.deepEqual(socket.sent[0], { type: 'start', configured_city: 'Hà Nội', history: [] });
  browser.captures[0].port.onmessage({ data: Uint8Array.of(0, 0).buffer });
  assert.equal(socket.sent[1].type, 'audio');
  socket.emit({ type: 'audio', data: 'AAA=', sample_rate: 24000 });
  assert.equal(browser.nodes.length, 1);
  socket.emit({ type: 'interrupted' });
  assert.equal(browser.nodes[0].stopped, true);
  assert.ok(events.some(item => item.type === 'interrupted'));
  session.stop(); session.stop();
  assert.equal(closed.length, 1);
  assert.ok(browser.tracks.every(track => track.stopped));
  assert.equal(browser.contexts[0].state, 'closed');
  assert.equal(browser.captures[0].port.onmessage, null);
});

test('cancelling while microphone permission is pending stops a late stream and opens no socket', async () => {
  const browser = browserHarness();
  let grant;
  navigator.mediaDevices.getUserMedia = () => new Promise(resolve => { grant = resolve; });
  const controller = new AbortController();
  const promise = startGeminiLive({ signal: controller.signal });
  await new Promise(resolve => setTimeout(resolve, 0));
  controller.abort();
  grant(browser.stream);
  await assert.rejects(promise, /Aborted/);
  assert.ok(browser.tracks.every(track => track.stopped));
  assert.equal(browser.sockets.length, 0);
});

test('runaway output buffering closes the session and releases its microphone', async () => {
  const browser = browserHarness();
  const closed = [];
  await startGeminiLive({ onClose: reason => closed.push(reason) });
  const frame = bytesToBase64(new Uint8Array(24000 * 2));
  for (let i = 0; i < 16; i++) browser.sockets[0].emit({ type: 'audio', data: frame, sample_rate: 24000 });
  assert.equal(closed.length, 1);
  assert.match(closed[0], /Âm thanh bị chậm/);
  assert.ok(browser.tracks.every(track => track.stopped));
});
