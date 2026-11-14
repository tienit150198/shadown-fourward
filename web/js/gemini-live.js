import { GPTLiveTransport } from './gpt-live.js';
import { OpenAILiveTransport } from './openai-live.js';
import { geminiLiveError } from './gemini-errors.js';
import { pcmBase64, decodePcm16 } from './live-pcm.js';
import { LiveTrace } from './live-trace.js';

export class GeminiLiveSession {
  constructor(callbacks = {}, { fetchImpl = globalThis.fetch, getFrame = () => null, getHistory = () => [], getTaskContext = () => ({}), getCity = () => "", getLocation = () => null } = {}) {
    this.callbacks = callbacks;
    // Window.fetch requires the Window receiver in browsers.
    this.fetchImpl = fetchImpl.bind(globalThis);
    this.getFrame = getFrame;
    this.getHistory = getHistory;
    this.getTaskContext = getTaskContext;
    this.getLocation = getLocation;
    this.getCity = getCity;
    this.completedTools = new Map();
    this.notices = [];
    this.status = "idle";
    this.generation = 0;
    this.sources = new Set();
    this.turnNumber = 0;
    this.awaitingTurn = true;
    this.newTurn();
  }

  setState(status, detail = "") {
    if (status !== this.status) this.recordTrace('state', {status});
    this.status = status;
    this.callbacks.onState?.(status, detail);
    if (status === 'listening') queueMicrotask(() => this.flushNotice());
  }

  recordTrace(kind, fields = {}) {
    this.trace?.record(kind, {turn:this.turn?.id || 0, ...fields});
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
    this.openaiTransport?.resumeAudio()?.then(() => this.callbacks.onAudioBlocked?.(false)).catch(() => this.callbacks.onAudioBlocked?.(true));
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
        throw new Error("Trình duyệt chưa hỗ trợ trò chuyện trực tiếp. Hãy mở bằng Chrome hoặc Safari mới.");
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
        method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ history, configured_city: this.getCity() }),
        signal: AbortSignal.any([this.abort.signal, AbortSignal.timeout(20000)]),
      });
      const session = await response.json();
      if (!response.ok) throw geminiLiveError(session,response.status);
      if (generation !== this.generation) return;
      this.toolSessionId = session.toolSessionId;
      this.trace = session.traceEnabled ? new LiveTrace(session.toolSessionId, this.fetchImpl) : null;
      this.toolLabels = session.toolLabels || {};
      this.completedTools.clear();
      if (['openai','openai_live'].includes(session.provider)) {
        const transport = session.provider === 'openai_live' ? new GPTLiveTransport(this) : new OpenAILiveTransport(this);
        this.openaiTransport = transport;
        this.socket = transport;
        await transport.connect(session, stream, AbortSignal.any([this.abort.signal, AbortSignal.timeout(20000)]));
        if (generation !== this.generation) return;
        this.limit = setTimeout(() => this.stop("Đã hết phiên 5 phút. Bấm micro để trò chuyện tiếp."), session.maxSeconds * 1000);
        this.setState('listening');
        return;
      }
      const socket = new WebSocket(session.socketUrl + "?access_token=" + encodeURIComponent(session.token));
      this.socket = socket;
      socket.binaryType = "arraybuffer";
      await new Promise((resolve, reject) => {
        const timeout = setTimeout(() => reject(new Error("Chưa kết nối được cuộc trò chuyện. Bạn thử lại nhé.")), 15000);
        this.setupReject = reject;
        socket.onopen = () => this.send({ setup: { model: "models/" + session.model } });
        socket.onmessage = (event) => {
          if (generation !== this.generation) return;
          try {
            const message = JSON.parse(typeof event.data === "string" ? event.data : new TextDecoder().decode(event.data));
            if (message.setupComplete) { clearTimeout(timeout); this.setupReject = null; resolve(); }
            if (message.error) throw geminiLiveError(message);
            this.handleMessage(message);
          } catch (error) { this.fail(error); }
        };
        socket.onerror = () => { clearTimeout(timeout); reject(new Error("Kết nối trò chuyện gặp lỗi. Bạn thử lại nhé.")); };
        socket.onclose = (event) => {
          clearTimeout(timeout);
          if (generation !== this.generation) return;
          if (event?.code && ![1000,1001].includes(event.code)) {
            const error=geminiLiveError({reason:event.reason});
            reject(error); this.fail(error);
          } else {
            reject(new Error('Cuộc trò chuyện đã ngắt kết nối. Bạn có thể mở lại micro.'));
            this.stop('Phiên Live đã đóng. Bấm micro để bắt đầu lại.');
          }
        };
      });
      if (generation !== this.generation) return;
      if (history.length) this.send({ clientContent: {
        turns: history.map((turn) => ({ role: turn.role === 'assistant' ? 'model' : 'user', parts: [{ text: turn.text }] })),
        turnComplete: false,
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
        this.fail(error.code ? error : new Error(messages[error.name] || error.message));
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
    this.recordTrace('transcript', {role:'user', text});
    this.send({ realtimeInput: { text } });
    this.setState('thinking');
    return true;
  }

  notifyTaskResult(execution) {
    if (this.status === 'idle') return;
    this.recordTrace('task_queued', {text:execution.speech || execution.message, status:execution.status,
      guide_session_id:execution.guide_session_id, step:execution.step});
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
    this.recordTrace('task_sent', {text:execution.speech || execution.message, status:execution.status,
      guide_session_id:execution.guide_session_id, step:execution.step});
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
      this.recordTrace('transcript', {role:'user', text});

    }
    for (const call of message.toolCall?.functionCalls || []) this.handleTool(call).catch((error) => this.fail(error));
    if (content?.outputTranscription?.text) {
      this.callbacks.onTranscript?.("assistant", content.outputTranscription.text);
      this.recordTrace('transcript', {role:'assistant', text:content.outputTranscription.text});
    }
    for (const part of content?.modelTurn?.parts || []) {
      if (!part.inlineData?.data || !part.inlineData.mimeType?.startsWith("audio/pcm")) continue;
      // Tools are on demand. Ordinary speech is never held behind a classifier.
      this.playAudio(part.inlineData);
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
      const location = await this.getLocation();
      if (turn.controller.signal.aborted || generation !== this.generation) return null;
      const response = await this.fetchImpl('/api/live/classify', {
        method: "POST", headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ text: request, ...this.getTaskContext(), ...(location ? { location } : {}),
          ...(itemIds?.length || itemId ? { item_ids: itemIds?.length ? itemIds.slice(0, 3) : [itemId] } : {}),
          ...(this.getHistory().length ? { history: this.getHistory().slice(-6) } : {}),
        }),
        signal: AbortSignal.any([turn.controller.signal, AbortSignal.timeout(35000)]),
      });
      const result = await response.json();
      if (!response.ok) throw new Error(result.detail || result.error?.message || "Chưa phân tích được yêu cầu. Bạn thử lại nhé.");
      if (generation !== this.generation || turn !== this.turn || turn.controller.signal.aborted) return null;
      const execution = await (this.callbacks.onClassification || this.callbacks.onCameraTask)?.(result, { request: turn.input || request, signal: turn.controller.signal, itemId, itemIds });
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

  async handleLegacyTool(call) {
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

  async handleTool(call) {
    // Keep a temporary adapter for already issued sessions with the old declaration.
    if (call.name === 'classify_request') return this.handleLegacyTool(call);
    if (!call || typeof call.id !== 'string' || typeof call.name !== 'string' || !call.args || typeof call.args !== 'object' || Array.isArray(call.args)) throw new Error('Yêu cầu công cụ không hợp lệ.');
    if (this.awaitingTurn) this.newTurn();
    if (this.tools?.has(call.id)) return;
    const previous = this.completedTools.get(call.id);
    const fingerprint = JSON.stringify({ name: call.name, args: call.args });
    if (previous) {
      if (previous.fingerprint !== fingerprint) throw new Error('Mã gọi công cụ bị trùng.');
      this.send({ toolResponse: { functionResponses: [previous.response] } });
      return;
    }
    if (!this.toolSessionId) throw new Error('Phiên công cụ chưa sẵn sàng. Hãy mở lại micro.');
    this.turn.toolCount = (this.turn.toolCount || 0) + 1;
    if (this.turn.toolCount > 12) throw new Error('Đã đạt giới hạn công cụ của lượt.');
    const turn = this.turn, generation = this.generation;
    this.recordTrace('tool_start', {name:call.name, text:call.name === 'lesson_control' ? call.args.command : ''});
    const controller = new AbortController();
    const signal = AbortSignal.any([controller.signal, turn.controller.signal, this.abort?.signal || controller.signal, AbortSignal.timeout(95000)]);
    this.tools ??= new Map();
    this.tools.set(call.id, { controller });
    const labels = this.toolLabels?.[call.name] || ['Đang kiểm tra…', 'Đã kiểm tra'];
    const visible = !['showcase_outfits','open_screen'].includes(call.name);
    const emit = event => { if (generation === this.generation && turn === this.turn && this.status !== 'idle') this.callbacks.onToolEvent?.(event); };
    if (visible) emit({ type:'tool_status', id:call.id, state:'running', label:labels[0] });
    this.setState('thinking');
    try {
      const location = call.name === 'get_weather' ? await this.getLocation() : null;
      if (signal.aborted || generation !== this.generation || turn !== this.turn || this.status === 'idle') return;
      const response = await this.fetchImpl('/api/live/tool', {
        method:'POST', headers:{ 'Content-Type':'application/json' },
        body:JSON.stringify({session_id:this.toolSessionId,call_id:call.id,name:call.name,arguments:call.args,
          ...(call.name === 'get_weather' ? {location} : {}), request_text: turn.input,
          ...this.getTaskContext(),history:this.getHistory().slice(-6).filter(turn => turn.text?.trim())}), signal,
      });
      const data = await response.json();
      if (!response.ok) throw new Error(data.message || (typeof data.detail === 'string' ? data.detail : '') || 'Chưa thực hiện được công cụ.');
      if (signal.aborted || generation !== this.generation || turn !== this.turn || this.status === 'idle') return;
      const result = data.result;
      if (!result || typeof result !== 'object') throw new Error('Kết quả công cụ chưa hợp lệ.');
      if (result.lesson_control) {
        result.execution = await this.callbacks.onLessonControl?.(result.lesson_control, {signal});
        if (!result.execution) result.error='Lesson control unavailable; do not claim success.';
        else if (result.execution.error) result.error=result.execution.error;
      }
      if (result.live_control) {
        result.execution = await this.callbacks.onLiveControl?.(result.live_control, {signal});
        if (!result.execution) result.error = 'Live view control is unavailable. Do not claim it succeeded.';
      }
      if (result.camera_decision) {
        const execute = this.callbacks.onCameraTask || this.callbacks.onClassification;
        const execution = await execute?.(result.camera_decision,{request:call.args.request || turn.input,signal,itemIds:call.args.item_ids});
        if (execution) result.camera_decision.context = {...result.camera_decision.context,execution};
      }
      if (signal.aborted || generation !== this.generation || turn !== this.turn || this.status === 'idle') return;
      if (visible) emit({type:'tool_status',id:call.id,state:result.error ? 'error':'done',label:result.error ? 'Chưa hoàn tất thao tác':labels[1]});
      for (const event of data.events || []) emit(event);
      const toolResponse={id:call.id,name:call.name,response:result.camera_decision || result};
      this.recordTrace('tool_result', {name:call.name, status:result.error ? 'error' : result.execution?.status || 'done'});
      this.completedTools.set(call.id,{fingerprint,response:toolResponse});
      if (this.completedTools.size > 60) this.completedTools.delete(this.completedTools.keys().next().value);
      this.send({toolResponse:{functionResponses:[toolResponse]}});
      this.setState('thinking');
    } catch (error) {
      if (visible) emit({type:'tool_status',id:call.id,state:'error',label:signal.aborted ? 'Đã huỷ thao tác':'Chưa hoàn tất thao tác'});
      if (!signal.aborted && generation === this.generation && turn === this.turn) throw error;
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
    this.recordTrace('error', {text:error?.message});
    this.stop();
    this.callbacks.onError?.(error);
  }

  stop(detail = "Đã dừng. Bấm micro để bắt đầu cuộc trò chuyện mới.") {
    this.trace?.close();
    this.trace = null;
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
    this.openaiTransport = null;
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
