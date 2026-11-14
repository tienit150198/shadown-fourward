import { OpenAILiveTransport } from './openai-live.js';
import { geminiLiveError } from './gemini-errors.js';

// GPT Live voice with a managed Responses backend. Media stays on WebRTC;
// all session configuration and permanent credentials stay on our server.
export class GPTLiveTransport extends OpenAILiveTransport {
  constructor(owner) {
    super(owner);
    this.waitForSession = true;
    this.calls = new Map();
    this.responses = new Map();
  }

  async negotiate(session, signal) {
    if (this.peer.iceGatheringState !== 'complete') {
      await new Promise((resolve, reject) => {
        const finish = error => {
          clearTimeout(timer);
          signal.removeEventListener('abort', cancel);
          this.peer.removeEventListener('icegatheringstatechange', check);
          error ? reject(error) : resolve();
        };
        const check = () => { if (this.peer.iceGatheringState === 'complete') finish(); };
        const cancel = () => finish(new Error('Đã hủy kết nối.'));
        const timer = setTimeout(() => finish(new Error('Chưa lấy được kết nối mạng cho GPT Live.')), 10000);
        signal.addEventListener('abort', cancel, { once: true });
        this.peer.addEventListener('icegatheringstatechange', check);
        check();
      });
    }
    const response = await this.owner.fetchImpl(session.connectUrl, {
      method: 'POST', headers: { 'Content-Type': 'application/json' }, signal,
      body: JSON.stringify({ session_id: session.toolSessionId, sdp: this.peer.localDescription.sdp }),
    });
    const result = await response.json();
    if (!response.ok) throw geminiLiveError(result, response.status);
    await this.peer.setRemoteDescription({ type: 'answer', sdp: result.transport.sdp });
  }

  frame() {
    // RTCDataChannel messages have a negotiated size cap. Shrink the JPEG to
    // fit one event rather than handing a large camera frame to channel.send.
    const frame = this.owner.getFrame();
    if (typeof frame !== 'string' || !frame.startsWith('data:image/jpeg;base64,')) return;
    const maxSize = Math.min(this.peer?.sctp?.maxMessageSize || 65536, 65536) - 2048;
    const queueImage = image => this.emit({ type: 'response.item.create', item: {
      type: 'message', role: 'user', content: [{ type: 'input_image', image_url: image, detail: 'low' }],
    } });
    if (frame.length <= maxSize) { queueImage(frame); return; }
    return new Promise(resolve => {
    const image = new Image();
    image.onerror = () => resolve();
    image.onload = () => {
      if (this.readyState !== 1) { resolve(); return; }
      const canvas = document.createElement('canvas');
      const scale = Math.min(1, 448 / Math.max(image.width, image.height));
      canvas.width = Math.round(image.width * scale);
      canvas.height = Math.round(image.height * scale);
      canvas.getContext('2d').drawImage(image, 0, 0, canvas.width, canvas.height);
      const data = canvas.toDataURL('image/jpeg', 0.45);
      if (data.length <= maxSize) queueImage(data);
      resolve();
    };
    image.src = frame;
    });
  }

  send(raw) {
    const message = JSON.parse(raw);
    const text = message.realtimeInput?.text;
    if (text) {
      if (text.startsWith('[SHADOW_TASK_RESULT]')) {
        this.emit({ type: 'session.commentary.append', delegation_id: null, content: text.slice(0, 1400) });
      } else {
        const sendText = () => {
          if (this.readyState !== 1) return;
          this.emit({ type: 'response.item.create', item: { type: 'message', role: 'user',
            content: [{ type: 'input_text', text }] } });
          this.emit({ type: 'response.create' });
        };
        const image = this.frame();
        if (image?.then) image.then(sendText).catch(error => this.owner.fail(error));
        else sendText();
      }
    }
    for (const result of message.toolResponse?.functionResponses || []) {
      const responseId = this.calls.get(result.id);
      if (!responseId) continue;
      this.emit({ type: 'response.item.create', item: { type: 'function_call_output',
        call_id: result.id, output: JSON.stringify(result.response) } });
      this.calls.delete(result.id);
      const response = this.responses.get(responseId);
      response.pending.delete(result.id);
      this.continueResponse(responseId);
    }
  }

  continueResponse(id) {
    const response = this.responses.get(id);
    if (!response?.done || response.pending.size || !response.hasCalls || response.continued) return;
    response.continued = true;
    this.emit({ type: 'response.create' });
    this.responses.delete(id);
  }

  backend(envelope) {
    const event = envelope.event;
    if (!event) return;
    if (event.type === 'response.created') {
      this.activeResponse = event.response.id;
      this.responses.set(this.activeResponse, { pending: new Set(), hasCalls: false, done: false });
    }
    const id = event.response_id || event.response?.id || this.activeResponse;
    if (event.type === 'response.output_item.done' && event.item?.type === 'function_call') {
      const item = event.item;
      if (this.calls.has(item.call_id)) return;
      let response = this.responses.get(id);
      if (!response) { response = { pending: new Set(), hasCalls: false, done: false }; this.responses.set(id, response); }
      response.hasCalls = true;
      response.pending.add(item.call_id);
      this.calls.set(item.call_id, id);
      this.owner.setState('thinking');
      this.owner.handleTool({ id: item.call_id, name: item.name, args: JSON.parse(item.arguments) })
        .catch(error => this.owner.fail(error));
    }
    if (event.type === 'response.completed') {
      const response = this.responses.get(id);
      if (response) {
        response.done = true;
        this.continueResponse(id);
        if (!response.hasCalls) this.responses.delete(id);
      }
    }
    if (['response.failed', 'response.incomplete', 'error'].includes(event.type)) {
      this.owner.fail(new Error('Backend GPT Live chưa hoàn tất yêu cầu. Hãy thử lại.'));
    }
  }

  message(event) {
    if (this.closing) {
      if (event.type === 'session.closed') { clearTimeout(this.closeTimer); super.close(); }
      return;
    }
    const owner = this.owner;
    if (event.type === 'session.started') {
      this.sessionReady?.();
      this.frame();
    } else if (event.type === 'response.event') {
      this.backend(event);
    } else if (event.type === 'session.delegation.created') {
      owner.setState('thinking');
    } else if (['session.input_transcript.delta','session.output_transcript.delta'].includes(event.type)) {
      const role = event.type === 'session.input_transcript.delta' ? 'user' : 'assistant';
      if (role === 'user') {
        if (owner.awaitingTurn || (this.lastUserEnd != null && event.start_ms - this.lastUserEnd > 1800 && !this.calls.size)) {
          owner.callbacks.onTurnComplete?.();
          owner.newTurn();
          this.frame();
        }
        this.lastUserEnd = event.end_ms;
        owner.turn.input += event.delta;
      }
      owner.callbacks.onTranscript?.(role, event.delta);
      owner.recordTrace('transcript', { role, text: event.delta });
      owner.setState(role === 'assistant' ? 'speaking' : 'listening');
      clearTimeout(this.turnTimer);
      this.turnTimer = setTimeout(() => {
        if (this.calls.size || this.readyState !== 1) return;
        owner.callbacks.onTurnComplete?.();
        owner.awaitingTurn = true;
        owner.setState('listening');
      }, 2500);
    } else if (event.type === 'session.closed') {
      owner.stop('Phiên GPT Live đã kết thúc. Bấm micro để bắt đầu lại.');
    } else if (event.type === 'error') {
      owner.fail(new Error('GPT Live gặp lỗi (' + (event.error?.code || 'unknown') + '). Hãy thử lại.'));
    }
  }

  close() {
    clearTimeout(this.turnTimer);
    if (this.closing) return;
    this.closing = true;
    if (this.readyState === 1 && this.channel?.readyState === 'open') {
      this.emit({ type: 'session.close' });
      this.readyState = 3;
      this.audio?.pause();
      this.closeTimer = setTimeout(() => super.close(), 5000);
    } else super.close();
  }
}
