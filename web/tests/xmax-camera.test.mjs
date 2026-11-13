import test from 'node:test';
import assert from 'node:assert/strict';
import { connectXmaxVideo } from '../js/xmax-camera.js';

test('a first-frame timeout retries once without replacing the manager, camera, or generation context', async () => {
  let connects = 0, generates = 0, disconnects = 0, retries = 0;
  const camera = {}, context = {}, view = {};
  const manager = {
    async connect(stream) { assert.equal(stream, camera); return { videoTrack: ++connects }; },
    async startGeneration(options) {
      assert.equal(options.localStream, camera); assert.equal(options.context, context);
      if (++generates === 1) throw Error('Realtime generation start timed out');
      return 'video';
    },
    async disconnect() { disconnects++; },
  };
  assert.equal(await connectXmaxVideo(manager, camera, context, view, { onRetry: () => retries++ }), 'video');
  assert.equal(connects, 2); assert.equal(disconnects, 1); assert.equal(retries, 1); assert.equal(view.remoteTrack, 2);
});

test('provider failures cannot create an unbounded retry loop', async () => {
  let attempts = 0;
  const manager = { connect: async () => ({ videoTrack: {} }), startGeneration: async () => { attempts++; throw Error('timed out'); }, disconnect: async () => {} };
  await assert.rejects(connectXmaxVideo(manager, {}, {}, {}), /timed out/);
  assert.equal(attempts, 2);
});

test('invalid credentials are reported immediately instead of opening another session', async () => {
  let attempts = 0;
  const manager = { connect: async () => { attempts++; throw Error('Invalid API key'); } };
  await assert.rejects(connectXmaxVideo(manager, {}, {}, {}), /Invalid API key/);
  assert.equal(attempts, 1);
});
