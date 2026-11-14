import test from 'node:test';
import assert from 'node:assert/strict';
import { connectXmaxVideo, startSharedXmax, tryonErrorMessage } from '../js/xmax-camera.js';

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
  assert.equal(connects, 2); assert.equal(disconnects, 1); assert.equal(retries, 1); assert.equal(view.track, 2);
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

function fittingFixture() {
  let view, stateListener;
  const original = { readyState: 'live', getSettings: () => ({ width: 720, height: 1280 }), stop() { this.readyState = 'ended'; }, clone: () => clone };
  const clone = { readyState: 'live', stop() { this.readyState = 'ended'; } };
  const remote = {};
  const manager = {
    async setStateListener(listener) { stateListener = listener; },
    async createExternalVideoStream({videoTrack}) { assert.equal(videoTrack, clone); return { videoTrack: {} }; },
    async connect() { return { videoTrack: remote }; },
    async startGeneration() { stateListener({connectionState:'Generating'}); },
    async close() { clone.stop(); },
  };
  const sdk = {
    XmaxClient: class { createRealtimeManager() { return manager; } createStorageService() { return {uploadImage:async()=>({url:'https://example.test/ref.jpg'})}; } },
    XmaxConfiguration: class {}, RealtimeConfiguration: class {}, RealtimeVideoFormat: class {}, RealtimeContext: class {}, RealtimeModel:{x2_1:'x2.1'}, VideoContentMode:{fill:'fill'},
    XmaxRealtimeVideoView: class { constructor(options) { assert.equal(options.videoContentMode,'fill'); view=this; this.element={style:{}}; } attach() {} detach() {} },
  };
  const requests = [];
  const fetchImpl = async (path, options) => {
    requests.push(path);
    if(path.endsWith('/session')) { assert.deepEqual(JSON.parse(options.body).item_ids,['shirt','trousers']); return Response.json({token:'tk-test',environment:'global',prompt:'both clothes',reference_image_url:'/wardrobe-media/outfit.jpg',maxSeconds:120}); }
    if(path.endsWith('/close'))return Response.json({ok:true});
    return new Response(new Uint8Array([1,2,3]));
  };
  return {original,clone,remote,sdk,requests,fetchImpl,getView:()=>view,container:{hidden:true,style:{},replaceChildren(){}}};
}

test('fitting preserves the full-size camera until a real result frame and never owns the original track', async () => {
  const f=fittingFixture(); let resolved=false;
  const pending=startSharedXmax(['shirt','trousers'],{getVideoTracks:()=>[f.original]},f.container,{fetchImpl:f.fetchImpl,loadSdk:async()=>f.sdk});
  void pending.then(()=>resolved=true);
  await new Promise(resolve=>setImmediate(resolve));
  assert.equal(f.getView().track,f.remote);
  assert.equal(f.getView().element.style.width,'100%');
  assert.equal(f.getView().element.style.height,'100%');
  assert.equal(f.container.style.opacity,'0');
  assert.equal(resolved,false,'Generating state is not evidence of a displayed frame');
  assert.equal(f.original.readyState,'live');
  f.getView().frameDisplayHandler();
  const run=await pending;
  assert.equal(f.container.style.opacity,'1');
  await run.stop();
  assert.equal(f.original.readyState,'live');
  assert.equal(f.clone.readyState,'ended');
  assert.equal(f.container.hidden,true);
  assert.ok(f.requests.includes('/api/tryon/xmax/close'));
});

test('cancelling fitting before its first frame restores the original camera and closes the temporary key', async () => {
  const f=fittingFixture(), controller=new AbortController();
  const pending=startSharedXmax(['shirt','trousers'],{getVideoTracks:()=>[f.original]},f.container,{signal:controller.signal,fetchImpl:f.fetchImpl,loadSdk:async()=>f.sdk});
  const rejected=assert.rejects(pending,{name:'AbortError'});
  await new Promise(resolve=>setImmediate(resolve));
  controller.abort(); await rejected;
  assert.equal(f.original.readyState,'live');
  assert.equal(f.container.hidden,true);
  assert.ok(f.requests.includes('/api/tryon/xmax/close'));
});
