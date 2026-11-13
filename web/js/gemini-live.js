import { pcmBase64, decodePcm16 } from './live-pcm.js';

export class GeminiLiveSession {
  constructor(callbacks = {}, { fetchImpl = globalThis.fetch, getFrame = () => null, getHistory = () => [], getTaskContext = () => ({}) } = {}) {
    this.callbacks = callbacks;
    // Window.fetch requires the Window receiver in browsers.
    this.fetchImpl = fetchImpl.bind(globalThis);
    this.getFrame = getFrame;
    this.getHistory = getHistory;
    this.getTaskContext = getTaskContext;
    this.notices = [];
    this.status = "idle";
    this.generation = 0;
    this.sources = new Set();
    this.turnNumber = 0;
    this.awaitingTurn = true;
    this.newTurn();
  }

  setState(status, detail = "") {
    this.status = status;
    this.callbacks.onState?.(status, detail);
    if (status === 'listening') queueMicrotask(() => this.flushNotice());
  }

  newTurn() {
    this.turn?.controller?.abort();
    this.turn = { id: ++this.turnNumber, input: "", pendingAudio: [], classified: false, promise: null, controller: new AbortController() };
    this.awaitingTurn = false;
    this.callbacks.onTurnStart?.();
    return this.turn;
  }

  send(message) {
    if (this.socket?.readyState === 1) this.socket.send(JSON.stringify(message));
  }

  updateAudioAvailability() {
    const contexts = [this.inputContext, this.outputContext].filter(Boolean);
    this.audioBlocked = contexts.some(context => context.state !== 'running' && context.state !== 'closed');
    this.callbacks.onAudioBlocked?.(this.audioBlocked);
  }

  resumeAudio() {
    // A page load can leave resume() pending until a gesture. Never let that
    // prevent requesting media permission or connecting the Live session.
    const contexts = [this.inputContext, this.outputContext].filter(context => context && context.state !== 'closed');
    for (const context of contexts) {
      if (context.state !== 'running') void context.resume().then(() => this.updateAudioAvailability()).catch(() => {});
    }
    this.updateAudioAvailability();
  }

  async start() {
    if (this.status !== "idle") return;
    const generation = ++this.generation;
    this.awaitingTurn = true;
    this.setState("connecting");
    try {
      if (!navigator.mediaDevices?.getUserMedia || !globalThis.AudioWorkletNode) {
        throw new Error("Trình duyệt chưa hỗ trợ Gemini Live. Hãy mở bằng Chrome hoặc Safari mới.");
      }
      this.inputContext = new AudioContext({ sampleRate: 16000 });
      this.outputContext = new AudioContext({ sampleRate: 24000 });
      this.inputContext.onstatechange = this.outputContext.onstatechange = () => this.updateAudioAvailability();
      this.resumeAudio();
      const stream = await navigator.mediaDevices.getUserMedia({ audio: { channelCount: 1, echoCancellation: true, noiseSuppression: true, autoGainControl: true } });
      if (generation !== this.generation) { stream.getTracks().forEach((track) => track.stop()); return; }
      this.stream = stream;
      // Active microphone capture allows autoplay in some browsers.
      this.resumeAudio();
      this.abort = new AbortController();
      const history = this.getHistory().slice(-6);
      const response = await this.fetchImpl("/api/live/session", {
        method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ history }),
        signal: AbortSignal.any([this.abort.signal, AbortSignal.timeout(20000)]),
      });
      const session = await response.json();
      if (!response.ok) throw new Error(session.detail || session.error?.message || "Không tạo được phiên Gemini Live.");
      if (generation !== this.generation) return;
      const socket = new WebSocket(session.socketUrl + "?access_token=" + encodeURIComponent(session.token));
      this.socket = socket;
      socket.binaryType = "arraybuffer";
      await new Promise((resolve, reject) => {
        const timeout = setTimeout(() => reject(new Error("Gemini Live chưa kết nối được. Hãy thử lại.")), 15000);
        this.setupReject = reject;
        socket.onopen = () => this.send({ setup: { model: "models/" + session.model } });
        socket.onmessage = (event) => {
          if (generation !== this.generation) return;
          try {
            const message = JSON.parse(typeof event.data === "string" ? event.data : new TextDecoder().decode(event.data));
            if (message.setupComplete) { clearTimeout(timeout); this.setupReject = null; resolve(); }
            if (message.error) throw new Error("Gemini Live từ chối phiên kết nối. Hãy kiểm tra model và quota.");
            this.handleMessage(message);
          } catch (error) { this.fail(error); }
        };
        socket.onerror = () => { clearTimeout(timeout); reject(new Error("Kết nối Gemini Live gặp lỗi. Hãy thử lại.")); };
        socket.onclose = () => {
          clearTimeout(timeout);
          if (generation !== this.generation) return;
          reject(new Error("Gemini Live đã ngắt kết nối."));
          this.stop("Phiên Live đã đóng. Bấm micro để bắt đầu lại.");
        };
      });
      if (generation !== this.generation) return;
      if (history.length) this.send({ clientContent: {
        turns: history.map((turn) => ({ role: turn.role === 'assistant' ? 'model' : 'user', parts: [{ text: turn.text }] })),
        turnComplete: true,
      } });
      await this.inputContext.audioWorklet.addModule('/js/live-pcm-worklet.js');
      if (generation !== this.generation) return;
      this.capture = new AudioWorkletNode(this.inputContext, "microphone-pcm");
      this.input = this.inputContext.createMediaStreamSource(stream);
      this.muted = this.inputContext.createGain();
      this.muted.gain.value = 0;
      this.input.connect(this.capture).connect(this.muted).connect(this.inputContext.destination);
      this.capture.port.onmessage = ({ data }) => {
        if (generation !== this.generation || socket.readyState !== 1) return;
        if (socket.bufferedAmount > 512000) { this.fail(new Error("Mạng đang chậm, phiên Live đã dừng. Hãy thử lại.")); return; }
        this.send({ realtimeInput: { audio: { data: pcmBase64(data), mimeType: "audio/pcm;rate=16000" } } });
      };
      this.limit = setTimeout(() => this.stop("Đã hết phiên 5 phút. Bấm micro để trò chuyện tiếp."), session.maxSeconds * 1000);
      this.videoTimer = setInterval(() => {
        if (generation !== this.generation) return;
        const frame = this.getFrame();
        if (typeof frame === 'string' && frame.startsWith('data:image/jpeg;base64,') && frame.length <= 500000) {
          this.send({ realtimeInput: { video: { data: frame.split(',')[1], mimeType: 'image/jpeg' } } });
        }
      }, 1100);
      this.setState("listening");
    } catch (error) {
      if (generation === this.generation) {
        const messages = { NotAllowedError: "Cho phép dùng micro để bắt đầu trò chuyện.", NotFoundError: "Không tìm thấy micro.", NotReadableError: "Không mở được micro. Hãy thử lại." };
        this.fail(new Error(messages[error.name] || error.message));
      }
    }
  }

  sendText(text) {
    if (this.status === 'idle' || this.status === 'connecting' || this.socket?.readyState !== 1) return false;
    this.stopAudio();
    this.newTurn();
    this.turn.input = text;
    this.turn.clientTextPending = true;
    this.callbacks.onTranscript?.('user', text);
    this.send({ realtimeInput: { text } });
    this.setState('thinking');
    return true;
  }

  notifyTaskResult(execution) {
    if (this.status === 'idle') return;
    this.notices.push(execution);
    this.notices = this.notices.slice(-3);
    this.flushNotice();
  }

  flushNotice() {
    if (!this.notices.length || !this.awaitingTurn || this.sources.size || this.status !== 'listening') return;
    const execution = this.notices.shift();
    const turn = this.newTurn();
    turn.classified = true;
    turn.notification = execution;
    this.send({ realtimeInput: { text: '[SHADOW_TASK_RESULT] ' + JSON.stringify(execution) } });
    this.setState('thinking');
  }

  handleMessage(message) {
    for (const id of message.toolCallCancellation?.ids || []) {
      this.tools?.get(id)?.controller.abort();
    }
    const content = message.serverContent;
    if (content?.interrupted) {
      this.stopAudio();
      // A typed follow-up was already placed in its own turn before the server
      // acknowledged interruption of the previous response.
      this.awaitingTurn = !this.turn.clientTextPending;
    }
    if (content?.inputTranscription?.text) {
      if (this.awaitingTurn) this.newTurn();
      const text = content.inputTranscription.text;
      this.turn.input += text;
      this.callbacks.onTranscript?.("user", text);
      if (this.turn.pendingAudio.length && !this.turn.promise) this.classify(this.turn.input).catch(() => {});
    }
    for (const call of message.toolCall?.functionCalls || []) this.handleTool(call).catch((error) => this.fail(error));
    if (content?.outputTranscription?.text) this.callbacks.onTranscript?.("assistant", content.outputTranscription.text);
    for (const part of content?.modelTurn?.parts || []) {
      if (!part.inlineData?.data || !part.inlineData.mimeType?.startsWith("audio/pcm")) continue;
      if (this.turn.classified) this.playAudio(part.inlineData);
      else {
        this.turn.pendingAudio.push(part.inlineData);
        if (this.turn.input && !this.turn.promise) this.classify(this.turn.input).catch(() => {});
      }
    }
    if (content?.turnComplete && !content.interrupted && content.interactionStatus !== "IN_PROGRESS") {
      this.awaitingTurn = true;
      this.callbacks.onTurnComplete?.();
      if (!this.sources.size) this.setState("listening");
    }
    if (message.goAway) this.callbacks.onNotice?.("Phiên Live sắp hết hạn. Bạn có thể dừng rồi bắt đầu lại.");
  }

  classify(request, { itemId, itemIds } = {}) {
    const turn = this.turn;
    if (turn.promise) return turn.promise;
    const generation = this.generation;
    this.setState("classifying");
    turn.promise = (async () => {
      const response = await this.fetchImpl('/api/live/classify', {
        method: "POST", headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ text: request, ...this.getTaskContext(),
          ...(itemIds?.length || itemId ? { item_ids: itemIds?.length ? itemIds.slice(0, 3) : [itemId] } : {}),
          ...(this.getHistory().length ? { history: this.getHistory().slice(-6) } : {}),
        }),
        signal: AbortSignal.any([turn.controller.signal, AbortSignal.timeout(35000)]),
      });
      const result = await response.json();
      if (!response.ok) throw new Error(result.detail || result.error?.message || "Decisions chưa phân loại được yêu cầu.");
      if (generation !== this.generation || turn !== this.turn || turn.controller.signal.aborted) return null;
      const execution = await this.callbacks.onClassification?.(result, { request: turn.input || request, signal: turn.controller.signal, itemId, itemIds });
      if (generation !== this.generation || turn !== this.turn || turn.controller.signal.aborted) return null;
      if (execution) result.context = { ...result.context, execution };
      turn.classified = true;
      for (const audio of turn.pendingAudio.splice(0)) this.playAudio(audio);
      return result;
    })().catch((error) => {
      if (!turn.controller.signal.aborted && generation === this.generation) this.fail(error);
      return null;
    });
    return turn.promise;
  }

  async handleTool(call) {
    if (this.awaitingTurn) this.newTurn();
    const turn = this.turn;
    turn.clientTextPending = false;
    this.tools ??= new Map();
    this.tools.set(call.id, turn);
    try {
      if (call.name !== "classify_request" || typeof call.args?.request !== "string") throw new Error("Yêu cầu phân loại từ Live không hợp lệ.");
      const result = turn.notification
        ? { intent: 'other', context: { execution: turn.notification }, actions: [] }
        : await this.classify(call.args.request, { itemId: call.args.item_id, itemIds: call.args.item_ids });
      if (!result || turn !== this.turn || turn.controller.signal.aborted || this.status === "idle") return;
      this.send({ toolResponse: { functionResponses: [{
        id: call.id, name: call.name,
        response: { intent: result.intent, label: result.label, confidence: result.confidence,
          context: result.context, actions: result.actions },
      }] } });
      this.setState("thinking");
    } finally { this.tools.delete(call.id); }
  }

  playAudio(audio) {
    if (!this.outputContext || this.status === "idle") return;
    const samples = decodePcm16(audio.data);
    const rate = Number(/rate=(\d+)/.exec(audio.mimeType)?.[1] || 24000);
    const buffer = this.outputContext.createBuffer(1, samples.length, rate);
    buffer.copyToChannel(samples, 0);
    const source = this.outputContext.createBufferSource();
    source.buffer = buffer;
    source.connect(this.outputContext.destination);
    const start = Math.max(this.outputContext.currentTime + 0.015, this.nextAudioTime || 0);
    this.nextAudioTime = start + buffer.duration;
    this.sources.add(source);
    source.onended = () => {
      this.sources.delete(source);
      if (!this.sources.size && this.awaitingTurn && this.status !== "idle") this.setState("listening");
    };
    source.start(start);
    this.callbacks.onAudio?.(samples.length);
    this.setState("speaking");
  }

  stopAudio() {
    for (const source of this.sources) { source.onended = null; try { source.stop(); } catch { /* already ended */ } }
    this.sources.clear();
    this.nextAudioTime = 0;
  }

  fail(error) {
    this.stop();
    this.callbacks.onError?.(error);
  }

  stop(detail = "Đã dừng. Bấm micro để bắt đầu cuộc trò chuyện mới.") {
    ++this.generation;
    clearTimeout(this.limit);
    clearInterval(this.videoTimer);
    this.abort?.abort();
    this.turn.controller.abort();
    this.turn.pendingAudio.length = 0;
    this.setupReject?.(new Error("Đã hủy kết nối."));
    this.setupReject = null;
    this.socket?.close();
    this.socket = null;
    this.capture?.disconnect();
    if (this.capture) this.capture.port.onmessage = null;
    this.input?.disconnect();
    this.muted?.disconnect();
    this.stream?.getTracks().forEach((track) => track.stop());
    this.stream = null;
    this.stopAudio();
    this.inputContext?.close().catch(() => {});
    this.outputContext?.close().catch(() => {});
    if (this.inputContext) this.inputContext.onstatechange = null;
    if (this.outputContext) this.outputContext.onstatechange = null;
    this.inputContext = this.outputContext = null;
    this.updateAudioAvailability();
    this.awaitingTurn = true;
    this.notices.length = 0;
    this.setState("idle", detail);
  }
}
