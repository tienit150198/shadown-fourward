// Best-effort diagnostics: never block speech, tools, or camera on log delivery.
export class LiveTrace {
  constructor(sessionId, fetchImpl, { intervalMs = 2000 } = {}) {
    this.sessionId = sessionId;
    this.fetchImpl = fetchImpl;
    this.queue = [];
    this.seq = 0;
    this.timer = setInterval(() => void this.flush(), intervalMs);
  }

  record(kind, fields = {}) {
    if (this.closed) return;
    const text = String(fields.text || '').slice(0, 4000);
    const previous = this.queue.at(-1);
    if (kind === 'transcript' && previous?.kind === kind && previous.role === fields.role &&
        previous.turn === fields.turn && previous.text.length + text.length <= 4000) {
      previous.text += text;
      return;
    }
    if (this.queue.length >= 40) return;
    const event = { seq: ++this.seq, at_ms: Date.now(), kind, turn: fields.turn || 0, text };
    if (['user', 'assistant'].includes(fields.role)) event.role = fields.role;
    if (fields.name) event.name = String(fields.name).slice(0, 80);
    if (fields.status) event.status = String(fields.status).slice(0, 40);
    if (fields.guide_session_id) event.guide_session_id = fields.guide_session_id;
    if (Number.isInteger(fields.step) && fields.step >= 0) event.step = fields.step;
    this.queue.push(event);
  }

  async flush() {
    if (this.sending || !this.queue.length) return;
    this.sending = true;
    const events = [];
    // Stay below fetch keepalive's 64 KiB limit even with UTF-8 Vietnamese text.
    while (this.queue.length && events.length < 8) {
      const next = this.queue[0];
      const body = JSON.stringify({session_id:this.sessionId, events:[...events, next]});
      if (events.length && new TextEncoder().encode(body).length > 48000) break;
      events.push(this.queue.shift());
    }
    try {
      const response = await this.fetchImpl('/api/live/trace', {
        method: 'POST', headers: {'Content-Type':'application/json'},
        body: JSON.stringify({session_id:this.sessionId, events}), keepalive: true,
        signal: AbortSignal.timeout(3000),
      });
      if ([410, 429].includes(response.status)) { this.closed = true; this.queue.length = 0; clearInterval(this.timer); }
    } catch { /* Diagnostic delivery must not interrupt the Live session. */ }
    finally {
      this.sending = false;
      if (this.closed && this.queue.length) void this.flush();
    }
  }

  close() {
    this.record('stop');
    this.closed = true;
    clearInterval(this.timer);
    void this.flush();
  }
}
