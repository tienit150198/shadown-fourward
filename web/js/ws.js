// WebSocket client for /session/control with auto-reconnect (PLAN.md §5).

export function controlSocketUrl() {
  const proto = location.protocol === 'https:' ? 'wss:' : 'ws:';
  return `${proto}//${location.host}/session/control`;
}

export class ControlSocket {
  /**
   * @param {{onMessage?:(msg:object)=>void, onStatus?:(status:'connecting'|'open'|'closed')=>void, url?:string}} opts
   */
  constructor({ onMessage, onStatus, url } = {}) {
    this.url = url || controlSocketUrl();
    this.onMessage = onMessage || (() => {});
    this.onStatus = onStatus || (() => {});
    this.ws = null;
    this.closedByUser = false;
    this.attempt = 0;
    this.retryTimer = null;
    this.status = 'closed';
    this._onOnline = () => { if (!this.ws && !this.closedByUser) this._connectNow(); };
    window.addEventListener('online', this._onOnline);
  }

  connect() {
    this.closedByUser = false;
    this._connectNow();
  }

  _setStatus(status) {
    this.status = status;
    try { this.onStatus(status); } catch (err) { console.error(err); }
  }

  _connectNow() {
    clearTimeout(this.retryTimer);
    this.retryTimer = null;
    if (this.ws) return;
    this._setStatus('connecting');
    let ws;
    try {
      ws = new WebSocket(this.url);
    } catch (err) {
      console.warn('WS create failed', err);
      this._scheduleReconnect();
      return;
    }
    this.ws = ws;
    ws.onopen = () => {
      this.attempt = 0;
      this._setStatus('open');
    };
    ws.onmessage = (event) => {
      let msg;
      try { msg = JSON.parse(event.data); } catch { return; }
      if (msg && typeof msg === 'object') {
        try { this.onMessage(msg); } catch (err) { console.error('WS handler error', err); }
      }
    };
    ws.onerror = () => { /* onclose follows */ };
    ws.onclose = () => {
      if (this.ws === ws) this.ws = null;
      this._setStatus('closed');
      if (!this.closedByUser) this._scheduleReconnect();
    };
  }

  _scheduleReconnect() {
    if (this.closedByUser || this.retryTimer) return;
    const delay = Math.min(8000, 600 * 2 ** this.attempt) + Math.random() * 300;
    this.attempt += 1;
    this.retryTimer = setTimeout(() => { this.retryTimer = null; this._connectNow(); }, delay);
  }

  get isOpen() {
    return !!this.ws && this.ws.readyState === WebSocket.OPEN;
  }

  get bufferedAmount() {
    return this.ws ? this.ws.bufferedAmount : 0;
  }

  send(payload) {
    if (!this.isOpen) return false;
    try {
      this.ws.send(JSON.stringify(payload));
      return true;
    } catch (err) {
      console.warn('WS send failed', err);
      return false;
    }
  }

  close() {
    this.closedByUser = true;
    clearTimeout(this.retryTimer);
    this.retryTimer = null;
    window.removeEventListener('online', this._onOnline);
    const ws = this.ws;
    this.ws = null;
    if (ws) {
      try {
        if (ws.readyState === WebSocket.OPEN) ws.send(JSON.stringify({ type: 'session.stop' }));
      } catch { /* ignore */ }
      try { ws.close(1000, 'client leaving'); } catch { /* ignore */ }
    }
    this._setStatus('closed');
  }
}
