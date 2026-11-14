// OpenAI Realtime WebRTC transport. The permanent API key stays on the backend.
export class OpenAILiveTransport {
  constructor(owner) { this.owner = owner; this.readyState = 0; this.pendingCalls = 0; }

  emit(event) {
    if (this.channel?.readyState === 'open') this.channel.send(JSON.stringify(event));
  }

  frame() {
    const image = this.owner.getFrame();
    if (typeof image === 'string' && image.startsWith('data:image/jpeg;base64,') && image.length <= 500000) {
      this.emit({ type: 'conversation.item.create', item: { type: 'message', role: 'user',
        content: [{ type: 'input_image', image_url: image }] } });
    }
  }

  send(raw) {
    const message = JSON.parse(raw);
    if (message.realtimeInput?.text) {
      if (this.responding) this.emit({ type: 'response.cancel' });
      this.emit({ type: 'output_audio_buffer.clear' });
      this.frame();
      this.emit({ type: 'conversation.item.create', item: { type: 'message', role: 'user',
        content: [{ type: 'input_text', text: message.realtimeInput.text }] } });
      this.emit({ type: 'response.create' });
    }
    for (const result of message.toolResponse?.functionResponses || []) {
      this.emit({ type: 'conversation.item.create', item: { type: 'function_call_output',
        call_id: result.id, output: JSON.stringify(result.response) } });
      this.pendingCalls = Math.max(0, this.pendingCalls - 1);
      this.continueAfterTools();
    }
  }

  continueAfterTools() {
    if (this.waitingForTools && !this.pendingCalls && !this.responding) {
      this.waitingForTools = false;
      this.responding = true;
      this.emit({ type: 'response.create' });
    }
  }

  message(event) {
    const owner = this.owner;
    switch (event.type) {
      case 'error':
        if (['response_cancel_not_active', 'output_audio_buffer_clear_not_active'].includes(event.error?.code)) return;
        owner.fail(new Error(event.error?.message || 'Kết nối OpenAI Live gặp lỗi.'));
        break;
      case 'input_audio_buffer.speech_started':
        owner.newTurn();
        owner.setState('listening');
        this.frame();
        break;
      case 'input_audio_buffer.speech_stopped': owner.setState('thinking'); break;
      case 'conversation.item.input_audio_transcription.completed':
        owner.turn.input = event.transcript || '';
        owner.callbacks.onTranscript?.('user', owner.turn.input);
        owner.recordTrace('transcript', { role: 'user', text: owner.turn.input });
        break;
      case 'response.created': this.responding = true; break;
      case 'response.output_audio_transcript.delta':
        owner.callbacks.onTranscript?.('assistant', event.delta);
        owner.recordTrace('transcript', { role: 'assistant', text: event.delta });
        break;
      case 'output_audio_buffer.started': owner.setState('speaking'); break;
      case 'output_audio_buffer.stopped':
      case 'output_audio_buffer.cleared':
        if (owner.awaitingTurn) owner.setState('listening');
        break;
      case 'response.function_call_arguments.done': {
        this.pendingCalls++;
        this.waitingForTools = true;
        let args;
        try { args = JSON.parse(event.arguments); }
        catch { owner.fail(new Error('Đối số công cụ OpenAI chưa hợp lệ.')); return; }
        owner.handleTool({ id: event.call_id, name: event.name, args }).catch(error => owner.fail(error));
        break;
      }
      case 'response.done':
        this.responding = false;
        if (event.response?.status === 'failed') {
          owner.fail(new Error(event.response.status_details?.error?.message || 'OpenAI chưa trả lời được.'));
          return;
        }
        this.continueAfterTools();
        if (!this.waitingForTools && !this.pendingCalls && !this.responding) {
          owner.awaitingTurn = true;
          owner.callbacks.onTurnComplete?.();
          if (owner.status !== 'speaking') owner.setState('listening');
        }
        break;
    }
  }

  async connect(session, stream, signal) {
    this.peer = new RTCPeerConnection();
    this.audio = document.createElement('audio');
    this.audio.autoplay = true;
    this.peer.ontrack = event => {
      this.audio.srcObject = event.streams[0];
      this.audio.play().catch(() => this.owner.callbacks.onAudioBlocked?.(true));
    };
    for (const track of stream.getTracks()) this.peer.addTrack(track, stream);
    this.channel = this.peer.createDataChannel('oai-events');
    this.channel.onclose = () => {
      if (this.readyState === 1) this.owner.fail(new Error('Phiên OpenAI Live đã ngắt kết nối.'));
    };
    this.channel.onmessage = ({ data }) => {
      try { this.message(JSON.parse(data)); } catch (error) { this.owner.fail(error); }
    };
    const ready = new Promise((resolve, reject) => {
      const timeout = setTimeout(() => reject(new Error('Chưa kết nối được OpenAI Live.')), 15000);
      const cancel = () => { clearTimeout(timeout); reject(new Error('Đã hủy kết nối.')); };
      signal.addEventListener('abort', cancel, { once: true });
      this.channel.onopen = () => {
        if (!this.waitForSession) {
          clearTimeout(timeout);
          signal.removeEventListener('abort', cancel);
          this.readyState = 1;
          resolve();
        }
      };
      this.sessionReady = () => { clearTimeout(timeout); signal.removeEventListener('abort', cancel); this.readyState = 1; resolve(); };
      this.channel.onerror = () => { clearTimeout(timeout); reject(new Error('Kết nối OpenAI Live gặp lỗi.')); };
    });
    // Attach rejection handling while SDP exchange is in flight.
    ready.catch(() => {});
    this.peer.onconnectionstatechange = () => {
      if (this.peer.connectionState === 'failed') this.owner.fail(new Error('Mất kết nối OpenAI Live.'));
    };
    const offer = await this.peer.createOffer();
    this.offer = offer;
    await this.peer.setLocalDescription(offer);
    await this.negotiate(session, signal);
    await ready;
  }

  async negotiate(session, signal) {
    const offer = this.peer.localDescription || this.offer;
    const response = await this.owner.fetchImpl(session.callUrl, {
      method: 'POST', headers: { Authorization: `Bearer ${session.token}`, 'Content-Type': 'application/sdp' },
      body: offer.sdp, signal,
    });
    if (!response.ok) throw new Error(`OpenAI Live từ chối kết nối (${response.status}). Kiểm tra API credit và quyền truy cập model.`);
    await this.peer.setRemoteDescription({ type: 'answer', sdp: await response.text() });
  }

  resumeAudio() { return this.audio?.play(); }

  close() {
    this.readyState = 3;
    if (this.channel) this.channel.onmessage = null;
    this.channel?.close();
    if (this.peer) this.peer.onconnectionstatechange = null;
    this.peer?.close();
    this.audio?.pause();
    if (this.audio) this.audio.srcObject = null;
  }
}
