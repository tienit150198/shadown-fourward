import test from 'node:test';
import assert from 'node:assert/strict';
import { OpenAILiveTransport } from '../js/openai-live.js';

function fixture() {
  const sent = [], transcripts = [], states = [];
  const owner = { turn: { input: '' }, status: 'thinking', getFrame: () => 'data:image/jpeg;base64,abc',
    callbacks: { onTranscript: (...args) => transcripts.push(args), onTurnComplete: () => {} },
    recordTrace() {}, setState: state => { states.push(state); owner.status = state; },
    newTurn() { owner.turn = { input: '' }; }, fail(error) { throw error; } };
  const transport = new OpenAILiveTransport(owner);
  transport.channel = { readyState: 'open', send: raw => sent.push(JSON.parse(raw)) };
  return { owner, transport, sent, transcripts, states };
}

test('typed turns include the actual camera frame then request a response', () => {
  const { transport, sent } = fixture();
  transport.send(JSON.stringify({ realtimeInput: { text: 'Tôi đang cầm gì?' } }));
  assert.equal(sent[1].item.content[0].type, 'input_image');
  assert.equal(sent[2].item.content[0].text, 'Tôi đang cầm gì?');
  assert.equal(sent.at(-1).type, 'response.create');
});

test('parallel tool calls return real outputs before exactly one continuation', async () => {
  const { owner, transport, sent } = fixture();
  const calls = [];
  owner.handleTool = async call => calls.push(call);
  transport.message({ type: 'response.created' });
  for (const id of ['one', 'two']) transport.message({ type: 'response.function_call_arguments.done',
    call_id: id, name: 'list_wardrobe', arguments: '{"limit":50}' });
  transport.message({ type: 'response.done', response: { status: 'completed' } });
  assert.equal(sent.length, 0);
  for (const call of calls) transport.send(JSON.stringify({ toolResponse: { functionResponses: [
    { id: call.id, response: { items: [call.id] } },
  ] } }));
  assert.equal(sent.filter(e => e.type === 'response.create').length, 1);
  assert.equal(sent[0].item.call_id, 'one');
  assert.deepEqual(JSON.parse(sent[0].item.output), { items: ['one'] });
  assert.equal(transport.responding, true);
});

test('voice transcripts and interruption use existing conversation callbacks', () => {
  const { owner, transport, transcripts, states } = fixture();
  transport.message({ type: 'input_audio_buffer.speech_started' });
  transport.message({ type: 'conversation.item.input_audio_transcription.completed', transcript: 'Xin chào' });
  transport.message({ type: 'response.output_audio_transcript.delta', delta: 'Chào bạn' });
  assert.equal(owner.turn.input, 'Xin chào');
  assert.deepEqual(transcripts, [['user', 'Xin chào'], ['assistant', 'Chào bạn']]);
  assert.deepEqual(states, ['listening']);
});

test('WebRTC exchanges SDP with only the ephemeral key and closes all media', async () => {
  const { owner, transport } = fixture();
  const previousPeer = globalThis.RTCPeerConnection;
  const previousDocument = globalThis.document;
  const previousFetch = owner.fetchImpl;
  const calls = [];
  let peer, paused = false;
  const audio = { play: async () => {}, pause: () => { paused = true; } };
  globalThis.document = { createElement: () => audio };
  globalThis.RTCPeerConnection = class {
    constructor() { peer = this; this.connectionState = 'new'; }
    addTrack(track) { calls.push(track); }
    createDataChannel() { return (this.channel = { readyState: 'connecting', close() {} }); }
    async createOffer() { return { type: 'offer', sdp: 'local-offer' }; }
    async setLocalDescription() {}
    async setRemoteDescription(answer) { this.answer = answer; this.channel.onopen(); }
    close() { this.closed = true; }
  };
  owner.fetchImpl = async (url, options) => {
    assert.equal(url, 'https://api.openai.com/v1/realtime/calls');
    assert.equal(options.headers.Authorization, 'Bearer ek_short');
    assert.equal(options.body, 'local-offer');
    return { ok: true, text: async () => 'remote-answer' };
  };
  try {
    await transport.connect({ callUrl: 'https://api.openai.com/v1/realtime/calls', token: 'ek_short' },
      { getTracks: () => ['microphone'] }, new AbortController().signal);
    assert.equal(transport.readyState, 1);
    assert.deepEqual(peer.answer, { type: 'answer', sdp: 'remote-answer' });
    assert.deepEqual(calls, ['microphone']);
    transport.close();
    assert.equal(peer.closed, true);
    assert.equal(paused, true);
    assert.equal(transport.readyState, 3);
  } finally {
    globalThis.RTCPeerConnection = previousPeer;
    globalThis.document = previousDocument;
    owner.fetchImpl = previousFetch;
  }
});
