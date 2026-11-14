import test from 'node:test';
import assert from 'node:assert/strict';
import { LiveTrace } from '../js/live-trace.js';
import { GeminiLiveSession } from '../js/gemini-live.js';

test('transcript chunks coalesce and diagnostics exclude media and unrelated fields', async () => {
  const requests = [];
  const trace = new LiveTrace('session', async (url, options) => {
    requests.push({url, body:JSON.parse(options.body)});
    return {status:200};
  });
  try {
    trace.record('transcript', {turn:1, role:'user', text:'Dạy xong ', image:'private', audio:'private'});
    trace.record('transcript', {turn:1, role:'user', text:'rồi.'});
    await trace.flush();
    assert.equal(requests[0].url, '/api/live/trace');
    const [event] = requests[0].body.events;
    assert.equal(event.text, 'Dạy xong rồi.');
    assert.equal(event.image, undefined);
    assert.equal(event.audio, undefined);
  } finally { trace.close(); }
});

test('a stopped session drains queued logs after an in-flight request', async () => {
  let release;
  const batches = [];
  const trace = new LiveTrace('session', async (_url, options) => {
    batches.push(JSON.parse(options.body).events);
    if (batches.length === 1) await new Promise(resolve => { release=resolve; });
    return {status:200};
  });
  trace.record('transcript', {role:'assistant', text:'Đã lưu.'});
  const pending = trace.flush();
  trace.record('state', {status:'listening'});
  trace.close();
  release();
  await pending;
  await new Promise(resolve => setImmediate(resolve));
  assert.deepEqual(batches[1].map(event => event.kind), ['state', 'stop']);
});

test('diagnostic network failures do not interrupt normal Live transcripts', async () => {
  const spoken = [];
  const live = new GeminiLiveSession({onTranscript:(role, text) => spoken.push({role,text})});
  live.trace = new LiveTrace('session', async () => { throw new Error('offline'); });
  live.status = 'listening';
  live.handleMessage({serverContent:{inputTranscription:{text:'Xin chào'}, outputTranscription:{text:'Mình là sờ ki.'}}});
  await live.trace.flush();
  assert.equal(spoken.length, 2);
  assert.equal(live.status, 'listening');
  live.stop();
});

test('UTF-8 batches stay within keepalive limits and queues remain bounded', async () => {
  const sizes = [];
  const trace = new LiveTrace('session', async (_url, options) => {
    sizes.push(new TextEncoder().encode(options.body).length);
    return {status:200};
  });
  for (let i=0;i<50;i++) trace.record('transcript', {turn:i, role:'user', text:'ờ'.repeat(4000)});
  assert.equal(trace.queue.length, 40);
  trace.close();
  for (let i=0;i<20 && (trace.sending || trace.queue.length);i++) await new Promise(resolve => setImmediate(resolve));
  assert.ok(sizes.length > 1);
  assert.ok(sizes.every(size => size <= 48000));
  assert.equal(trace.queue.length, 0);
});
