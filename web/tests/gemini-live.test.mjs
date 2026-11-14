import test from 'node:test';
import assert from 'node:assert/strict';
import { GeminiLiveSession } from '../js/gemini-live.js';
import { Pcm16Encoder, pcmBase64, decodePcm16 } from '../js/live-pcm.js';

const decision = () => ({
  intent: 'guide_coffee', label: 'Hướng dẫn pha cà phê', confidence: .97,
  context: { lesson: { name: 'Pha cà phê', steps: [{ instruction: 'Tráng phin' }] } },
  actions: [{ route: 'guide', id: 'coffee', label: 'Mở bài hướng dẫn đã học' }],
});
const call = { id: 'call-1', name: 'classify_request', args: { request: 'Hướng dẫn tôi pha cà phê.' } };

test('Live waits for Decisions and forwards actual lesson context before allowing an answer', async () => {
  const events = [];
  const live = new GeminiLiveSession({ onClassification: () => events.push('classified') }, {
    fetchImpl: async (url, options) => {
      assert.equal(url, '/api/live/classify');
      assert.deepEqual(JSON.parse(options.body), { text: call.args.request });
      events.push('requested');
      return Response.json(decision());
    },
  });
  live.status = 'listening';
  live.socket = { readyState: 1, send(raw) {
    const response = JSON.parse(raw).toolResponse.functionResponses[0];
    assert.equal(response.id, call.id);
    assert.equal(response.response.intent, 'guide_coffee');
    assert.equal(response.response.context.lesson.steps[0].instruction, 'Tráng phin');
    assert.equal(response.response.actions[0].route, 'guide');
    events.push('tool-response');
  }, close() {} };
  await live.handleTool(call);
  assert.deepEqual(events, ['requested', 'classified', 'tool-response']);
  live.stop();
});

test('leaving the screen while Decisions is pending does not apply a stale result', async () => {
  let resolve;
  const events = [];
  const live = new GeminiLiveSession({ onClassification: () => events.push('stale-label') }, {
    fetchImpl: () => new Promise((done) => { resolve = done; }),
  });
  live.status = 'listening';
  live.socket = { readyState: 1, send() { events.push('stale-response'); }, close() {} };
  const pending = live.handleTool(call);
  await Promise.resolve();
  live.stop();
  resolve(Response.json(decision()));
  await pending;
  assert.deepEqual(events, []);
});

test('Live waits for the real camera action and reports execution status in its tool response', async () => {
  let completeAction;
  const sent = [];
  const live = new GeminiLiveSession({ onClassification: () => new Promise(resolve => { completeAction = resolve; }) }, {
    fetchImpl: async (_url, options) => {
      assert.equal(JSON.parse(options.body).current_task, 'teaching');
      return Response.json(decision());
    }, getTaskContext: () => ({ current_task: 'teaching', task_title: 'Pha cà phê' }),
  });
  live.status = 'listening';
  live.socket = { readyState: 1, send: raw => sent.push(JSON.parse(raw)), close() {} };
  const pending = live.handleTool(call);
  await new Promise(resolve => setImmediate(resolve));
  assert.equal(sent.length, 0);
  completeAction({ mode: 'compiling', status: 'compiling' });
  await pending;
  assert.equal(sent[0].toolResponse.functionResponses[0].response.context.execution.status, 'compiling');
  live.stop();
});

test('background task completion waits for the user turn to finish before announcing the result', () => {
  const sent = [];
  const live = new GeminiLiveSession();
  live.status = 'thinking';
  live.socket = { readyState: 1, send: raw => sent.push(JSON.parse(raw)), close() {} };
  live.notifyTaskResult({ status: 'saved', message: 'Đã lưu bài dạy.' });
  assert.equal(sent.length, 0);
  live.status = 'listening';
  live.awaitingTurn = true;
  live.flushNotice();
  assert.match(sent[0].realtimeInput.text, /SHADOW_TASK_RESULT/);
  assert.equal(live.turn.classified, true);
  live.stop();
});

test('the existing text composer can send a follow-up into the same Live session', () => {
  const turns = [];
  const sent = [];
  const live = new GeminiLiveSession({ onTranscript: (role, text) => turns.push({ role, text }) });
  assert.equal(live.sendText('Tiếp theo làm gì?'), false);
  live.status = 'listening';
  live.socket = { readyState: 1, send(raw) { sent.push(JSON.parse(raw)); }, close() {} };
  assert.equal(live.sendText('Tiếp theo làm gì?'), true);
  assert.deepEqual(sent, [{ realtimeInput: { text: 'Tiếp theo làm gì?' } }]);
  assert.deepEqual(turns, [{ role: 'user', text: 'Tiếp theo làm gì?' }]);
  live.stop();
});

test('browser fetch retains the global receiver', async () => {
  const live = new GeminiLiveSession({}, { fetchImpl: function () {
    assert.equal(this, globalThis);
    return Promise.resolve(Response.json(decision()));
  } });
  live.status = 'listening';
  await live.classify(call.args.request);
  live.stop();
});

test('spoken fitting sends selection and recent recommendations to the real resolver', async () => {
  let body;
  const history = [{ role: 'assistant', text: 'Áo trắng với quần jeans xanh.' }];
  const live = new GeminiLiveSession({}, {
    fetchImpl: async (_url, options) => { body = JSON.parse(options.body); return Response.json(decision()); },
    getHistory: () => history, getTaskContext: () => ({ current_task: 'tryon', selected_item_ids: ['shirt'] }),
  });
  live.status = 'listening';
  await live.classify('Mặc bộ bạn vừa gợi ý', { itemIds: ['pants'] });
  assert.deepEqual(body.item_ids, ['pants']);
  assert.deepEqual(body.selected_item_ids, ['shirt']);
  assert.deepEqual(body.history, history);
  live.stop();
});

test('autoplay-blocked audio cannot prevent microphone permission, Live setup, or cleanup', async () => {
  const originals = Object.fromEntries(['navigator', 'AudioContext', 'AudioWorkletNode', 'WebSocket'].map(key => [key, Object.getOwnPropertyDescriptor(globalThis, key)]));
  let microphoneRequests = 0, stopped = 0, tokenRequests = 0;
  const contexts = [], audioStates = [];
  class Node {
    connect(target) { return target; }
    disconnect() {}
  }
  class Context {
    constructor() { this.state = 'suspended'; this.audioWorklet = { addModule: async () => {} }; contexts.push(this); }
    resume() { return new Promise(() => {}); }
    close() { this.state = 'closed'; return Promise.resolve(); }
    createMediaStreamSource() { return new Node(); }
    createGain() { return Object.assign(new Node(), { gain: { value: 1 } }); }
  }
  class Socket {
    constructor() { this.readyState = 1; queueMicrotask(() => this.onopen()); }
    send(raw) { if (JSON.parse(raw).setup) queueMicrotask(() => this.onmessage({ data: '{"setupComplete":{}}' })); }
    close() {}
  }
  const live = new GeminiLiveSession({ onAudioBlocked: value => audioStates.push(value) }, {
    fetchImpl: async () => { tokenRequests++; return Response.json({ token: 'temporary', model: 'live', socketUrl: 'wss://example.test', maxSeconds: 300 }); },
  });
  try {
    Object.defineProperty(globalThis, 'navigator', { configurable: true, value: { mediaDevices: { getUserMedia: async () => {
      microphoneRequests++; return { getTracks: () => [{ stop: () => stopped++ }] };
    } } } });
    Object.defineProperty(globalThis, 'AudioContext', { configurable: true, value: Context });
    Object.defineProperty(globalThis, 'AudioWorkletNode', { configurable: true, value: class extends Node { constructor() { super(); this.port = {}; } } });
    Object.defineProperty(globalThis, 'WebSocket', { configurable: true, value: Socket });
    await live.start();
    assert.equal(microphoneRequests, 1);
    assert.equal(tokenRequests, 1);
    assert.equal(live.status, 'listening');
    assert.equal(live.audioBlocked, true);
    // A user gesture resumes the existing session, rather than minting a new one.
    for (const context of contexts) { context.resume = async () => { context.state = 'running'; }; }
    live.resumeAudio();
    await new Promise(resolve => setImmediate(resolve));
    assert.equal(live.audioBlocked, false);
    assert.equal(tokenRequests, 1);
    live.stop();
    assert.equal(stopped, 1);
    assert.ok(contexts.every(context => context.state === 'closed'));
    assert.ok(audioStates.includes(true) && audioStates.includes(false));
  } finally {
    live.stop();
    for (const [key, descriptor] of Object.entries(originals)) {
      if (descriptor) Object.defineProperty(globalThis, key, descriptor);
      else delete globalThis[key];
    }
  }
});

test('an old tool cancellation cannot abort a newer typed follow-up', async () => {
  let resolve;
  const live = new GeminiLiveSession({}, { fetchImpl: () => new Promise((done) => { resolve = done; }) });
  live.status = 'listening';
  live.socket = { readyState: 1, send() {}, close() {} };
  const pending = live.handleTool(call);
  await Promise.resolve();
  live.sendText('Tôi muốn thêm áo vào tủ đồ.');
  const newerTurn = live.turn;
  live.handleMessage({ toolCallCancellation: { ids: [call.id] } });
  assert.equal(newerTurn.controller.signal.aborted, false);
  resolve(Response.json(decision()));
  await pending;
  live.stop();
});

test('acknowledging interruption preserves the already submitted text turn', () => {
  let turns = 0;
  const live = new GeminiLiveSession({ onTurnStart: () => { turns++; } });
  live.status = 'listening';
  live.socket = { readyState: 1, send() {}, close() {} };
  live.sendText('Tiếp theo làm gì?');
  const activeTurn = live.turn;
  const count = turns;
  live.handleMessage({ serverContent: { interrupted: true, turnComplete: true } });
  assert.equal(live.turn, activeTurn);
  assert.equal(live.awaitingTurn, false);
  assert.equal(turns, count);
  live.stop();
});

test('PCM resampling preserves sample count across browser callbacks', () => {
  const encoder = new Pcm16Encoder(48000, 16000, 160);
  const source = new Float32Array(4800).fill(.5);
  const frames = [];
  for (let i = 0; i < source.length; i += 128) frames.push(...encoder.push(source.subarray(i, i + 128)));
  assert.equal(frames.length, 10);
  for (const frame of frames) assert.ok(new Int16Array(frame).every((sample) => sample === 16384));
});

test('PCM playback reads signed little-endian samples and capture clamps safely', () => {
  const [frame] = new Pcm16Encoder(16000, 16000, 4).push(new Float32Array([-2, -1, .5, 2]));
  assert.deepEqual([...new Int16Array(frame)], [-32768, -32768, 16384, 32767]);
  assert.deepEqual([...decodePcm16(pcmBase64(frame))], [-1, -1, .5, 32767 / 32768]);
  assert.throws(() => decodePcm16(btoa('x')));
});


test('Live supplies device coordinates to the shared weather context', async () => {
  let body;
  const live = new GeminiLiveSession({}, {
    getLocation: async () => ({ lat: 10.8, lon: 106.6 }),
    fetchImpl: async (_, init) => { body = JSON.parse(init.body); return Response.json(decision()); },
  });
  await live.classify('Phối đồ ngày mai');
  assert.deepEqual(body.location, { lat: 10.8, lon: 106.6 });
  live.stop();
});

test('cancelling while GPS permission is pending never sends an obsolete turn', async () => {
  let locate;
  let requests = 0;
  const live = new GeminiLiveSession({}, {
    getLocation: () => new Promise(resolve => { locate = resolve; }),
    fetchImpl: async () => { requests++; return Response.json(decision()); },
  });
  const pending = live.classify('Phối đồ hôm nay');
  live.stop();
  locate({ lat: 10.8, lon: 106.6 });
  await pending;
  assert.equal(requests, 0);
});
