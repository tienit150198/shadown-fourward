import test from 'node:test';
import assert from 'node:assert/strict';
import { GPTLiveTransport } from '../js/gpt-live.js';

function setup() {
  const sent = [], transcripts = [];
  const owner = { getFrame: () => 'data:image/jpeg;base64,abc', turn: { input: '' },
    callbacks: { onTranscript: (...args) => transcripts.push(args), onTurnComplete() {} },
    recordTrace() {}, setState() {}, newTurn() { this.turn={ input:'' }; this.awaitingTurn=false; },
    fail(error) { throw error; }, handleTool: async () => {} };
  const transport = new GPTLiveTransport(owner);
  transport.readyState = 1;
  transport.channel = { readyState: 'open', send: raw => sent.push(JSON.parse(raw)) };
  return { owner, transport, sent, transcripts };
}

test('Live waits for session.started and queues camera images for Responses only', () => {
  const { transport, sent } = setup();
  let started = false;
  transport.sessionReady = () => { started = true; };
  assert.equal(transport.waitForSession, true);
  transport.message({ type: 'session.started' });
  assert.equal(started, true);
  assert.equal(sent[0].type, 'response.item.create');
  assert.equal(sent[0].item.content[0].type, 'input_image');
  assert.ok(sent.every(event => event.type !== 'session.start'));
});

test('Live typed input and task notifications use their documented event types', () => {
  const { transport, sent } = setup();
  transport.send(JSON.stringify({ realtimeInput: { text: 'Phối đồ cho tôi' } }));
  assert.equal(sent[1].item.content[0].text, 'Phối đồ cho tôi');
  assert.equal(sent[2].type, 'response.create');
  transport.send(JSON.stringify({ realtimeInput: { text: '[SHADOW_TASK_RESULT] Đã lưu bài' } }));
  assert.equal(sent[3].type, 'session.commentary.append');
  assert.equal(sent[3].delegation_id, null);
});

test('nested tool calls wait for every result before continuing once', async () => {
  const { owner, transport, sent } = setup();
  const calls = [];
  owner.handleTool = async call => calls.push(call);
  const nested = event => transport.message({ type: 'response.event', delegation_id:'d1', event });
  nested({ type: 'response.created', response: { id: 'r1' } });
  for (const id of ['c1','c2']) nested({ type: 'response.output_item.done', response_id:'r1',
    item:{ type:'function_call',call_id:id,name:'list_wardrobe',arguments:'{}' } });
  nested({ type:'response.completed',response:{id:'r1',output:[]} });
  assert.equal(sent.length,0);
  for (const call of calls) transport.send(JSON.stringify({toolResponse:{functionResponses:[
    {id:call.id,response:{items:[call.id]}}]}}));
  assert.equal(sent.filter(e => e.type === 'response.create').length,1);
  assert.equal(sent[0].item.call_id,'c1');
  assert.equal(transport.calls.size,0);
});

test('full duplex transcript deltas preserve both speakers', () => {
  const { owner, transport, transcripts } = setup();
  transport.message({ type:'session.input_transcript.delta', delta:'Xin ',start_ms:0,end_ms:100 });
  transport.message({ type:'session.output_transcript.delta', delta:'Chào bạn',start_ms:100,end_ms:200 });
  transport.message({ type:'session.input_transcript.delta', delta:'chào',start_ms:100,end_ms:300 });
  assert.equal(owner.turn.input,'Xin chào');
  assert.deepEqual(transcripts,[['user','Xin '],['assistant','Chào bạn'],['user','chào']]);
  clearTimeout(transport.turnTimer);
});
