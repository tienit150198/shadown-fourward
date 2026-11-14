import test from 'node:test';
import assert from 'node:assert/strict';
import { connectXmaxVideo, tryonErrorMessage } from '../js/xmax-camera.js';

test('provider errors do not reveal service names, addresses or credentials to users', () => {
  for (const message of ['Xmax X2.1 failed with tk-private', 'Xmax SDK tại https://api.xmax.ai lỗi uk-secret', 'Failed uploading https://xmax.ai/image']) {
    const displayed = tryonErrorMessage(new Error(message));
    assert.doesNotMatch(displayed, /xmax|x2\.1|https?:|(?:uk|tk)-/i);
    assert.ok(displayed.length > 0);
  }
  assert.match(tryonErrorMessage(new Error('Xmax connection timed out')), /mất nhiều thời gian/);
});

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

import { xmaxCreditCode, recoverXmaxSession } from '../js/xmax-camera.js';

test('credit detection handles SDK codes and balance errors without bypassing token caps', () => {
  assert.equal(xmaxCreditCode({apiCode:46003}),46003);
  assert.equal(xmaxCreditCode({apiCode:'46017'}),46017);
  assert.equal(xmaxCreditCode({httpStatus:402}),402);
  assert.equal(xmaxCreditCode(Error('Insufficient balance')),46017);
  assert.equal(xmaxCreditCode({apiCode:46019,message:'Insufficient credits'}),null);
  assert.equal(xmaxCreditCode(Error('Temporary key points limit reached')),null);
  assert.equal(xmaxCreditCode(Error('Invalid API key')),null);
});

test('startup credit failure retries with another session and stops at pool size', async () => {
  let attempts=0, stopped=0;
  const handle=await recoverXmaxSession(async ({onPoolSize}) => {
    onPoolSize(4);
    if (++attempts<4)throw Object.assign(Error('Insufficient credit'),{creditRetryable:true});
    return {stop:async()=>stopped++,getFrame:()=> 'frame'};
  });
  assert.equal(attempts,4);
  assert.equal(handle.getFrame(),'frame');
  await handle.stop();
  assert.equal(stopped,1);
  attempts=0;
  await assert.rejects(recoverXmaxSession(async ({onPoolSize})=>{
    onPoolSize(2);attempts++;
    throw Object.assign(Error('Insufficient credits'),{creditRetryable:true});
  }),/Insufficient/);
  assert.equal(attempts,2);
});

test('credit exhaustion during generation recovers and returns frames from the new session', async () => {
  let attempts=0, fail;
  const errors=[];
  const handle=await recoverXmaxSession(async ({onPoolSize,onFailure})=>{
    onPoolSize(2);fail=onFailure;
    const id=++attempts;
    return {stop:async()=>{},getFrame:()=>id};
  },{onError:error=>errors.push(error)});
  assert.equal(handle.getFrame(),1);
  await fail(Object.assign(Error('Insufficient credit'),{creditRetryable:true}));
  assert.equal(handle.getFrame(),2);
  assert.deepEqual(errors,[]);
  await handle.stop();
});

test('cancellation and noncredit failures never rotate keys', async () => {
  let attempts=0;
  await assert.rejects(recoverXmaxSession(async ({onPoolSize})=>{
    onPoolSize(4);attempts++;throw Error('Invalid API key');
  }),/Invalid/);
  assert.equal(attempts,1);
  const controller=new AbortController();controller.abort();
  await assert.rejects(recoverXmaxSession(async()=>{attempts++;},{signal:controller.signal}),{name:'AbortError'});
  assert.equal(attempts,1);
});
