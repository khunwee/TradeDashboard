// =============================================================================
// websocket.js — Real-Time WebSocket Connection Manager
// Handles: account feeds, portfolio feeds, reconnection, heartbeat
// =============================================================================

class WSManager {
  constructor() {
    this._sockets     = {};   // key → WebSocket instance
    this._handlers    = {};   // key → Map<event, [callbacks]>
    this._retries     = {};   // key → retry count
    this._timers      = {};   // key → reconnect timer
    this._heartbeats  = {};   // key → interval timer
    this._status      = {};   // key → 'connecting' | 'connected' | 'disconnected'
    this._maxRetries  = 10;
    this._baseDelay   = 1000; // ms
  }

  // ── Connect to account feed ───────────────────────────────────────────────
  connectAccount(accountId, onMessage) {
    const key = `account_${accountId}`;
    const token = localStorage.getItem('access_token');
    if (!token) return;

    const url = `${this._wsBase()}/ws/account/${accountId}?token=${encodeURIComponent(token)}`;
    this._connect(key, url, onMessage);
    return key;
  }

  // ── Connect to portfolio feed ─────────────────────────────────────────────
  connectPortfolio(onMessage) {
    const key = 'portfolio';
    const token = localStorage.getItem('access_token');
    if (!token) return;

    const url = `${this._wsBase()}/ws/portfolio?token=${encodeURIComponent(token)}`;
    this._connect(key, url, onMessage);
    return key;
  }

  // ── Internal connect logic ────────────────────────────────────────────────
  _connect(key, url, onMessage) {
    if (this._sockets[key] &&
        [WebSocket.CONNECTING, WebSocket.OPEN].includes(this._sockets[key].readyState)) {
      return; // Already connected or connecting
    }

    this._status[key] = 'connecting';
    this._emit(key, 'status', 'connecting');

    try {
      const ws = new WebSocket(url);
      this._sockets[key] = ws;

      ws.onopen = () => {
        this._status[key]  = 'connected';
        this._retries[key] = 0;
        this._emit(key, 'status', 'connected');
        this._startHeartbeat(key, ws);
        console.debug(`[WS] Connected: ${key}`);
      };

      ws.onmessage = (evt) => {
        try {
          const data = JSON.parse(evt.data);
          if (data !== 'pong') {
            onMessage && onMessage(data);
            this._emit(key, 'message', data);
          }
        } catch (e) {
          // plain text pong
        }
      };

      ws.onclose = (evt) => {
        this._status[key] = 'disconnected';
        this._emit(key, 'status', 'disconnected');
        this._stopHeartbeat(key);

        if (!evt.wasClean && evt.code !== 4001) {
          this._scheduleReconnect(key, url, onMessage);
        }
        console.debug(`[WS] Closed: ${key} (code: ${evt.code})`);
      };

      ws.onerror = (err) => {
        this._status[key] = 'error';
        this._emit(key, 'status', 'error');
        console.warn(`[WS] Error: ${key}`, err);
      };

    } catch (e) {
      console.error(`[WS] Failed to create socket for ${key}:`, e);
      this._scheduleReconnect(key, url, onMessage);
    }
  }

  // ── Heartbeat ping ────────────────────────────────────────────────────────
  _startHeartbeat(key, ws) {
    this._stopHeartbeat(key);
    this._heartbeats[key] = setInterval(() => {
      if (ws.readyState === WebSocket.OPEN) {
        ws.send('ping');
      }
    }, 20000); // Every 20 seconds
  }

  _stopHeartbeat(key) {
    if (this._heartbeats[key]) {
      clearInterval(this._heartbeats[key]);
      delete this._heartbeats[key];
    }
  }

  // ── Reconnection with exponential backoff ─────────────────────────────────
  _scheduleReconnect(key, url, onMessage) {
    const attempt = (this._retries[key] || 0) + 1;
    this._retries[key] = attempt;

    if (attempt > this._maxRetries) {
      console.warn(`[WS] Max retries reached for ${key}`);
      this._emit(key, 'status', 'failed');
      return;
    }

    const delay = Math.min(this._baseDelay * Math.pow(1.5, attempt), 30000);
    console.debug(`[WS] Reconnecting ${key} in ${delay}ms (attempt ${attempt})`);

    this._timers[key] = setTimeout(() => {
      this._connect(key, url, onMessage);
    }, delay);
  }

  // ── Disconnect ────────────────────────────────────────────────────────────
  disconnect(key) {
    if (this._timers[key]) {
      clearTimeout(this._timers[key]);
      delete this._timers[key];
    }
    this._stopHeartbeat(key);

    const ws = this._sockets[key];
    if (ws) {
      ws.onclose = null;  // prevent reconnect
      ws.close(1000, 'User disconnected');
      delete this._sockets[key];
    }
    this._status[key] = 'disconnected';
  }

  disconnectAll() {
    Object.keys(this._sockets).forEach(key => this.disconnect(key));
  }

  // ── Event Emitter ─────────────────────────────────────────────────────────
  on(key, event, callback) {
    if (!this._handlers[key]) this._handlers[key] = {};
    if (!this._handlers[key][event]) this._handlers[key][event] = [];
    this._handlers[key][event].push(callback);
    return this; // chainable
  }

  off(key, event, callback) {
    if (!this._handlers[key]?.[event]) return;
    this._handlers[key][event] = this._handlers[key][event].filter(cb => cb !== callback);
  }

  _emit(key, event, data) {
    const handlers = this._handlers[key]?.[event] || [];
    handlers.forEach(cb => { try { cb(data); } catch(e) {} });
  }

  // ── Status helpers ────────────────────────────────────────────────────────
  isConnected(key)    { return this._status[key] === 'connected'; }
  getStatus(key)      { return this._status[key] || 'disconnected'; }

  // ── WebSocket URL base ────────────────────────────────────────────────────
  _wsBase() {
    const proto = location.protocol === 'https:' ? 'wss' : 'ws';
    return `${proto}://${location.host}`;
  }
}

// ── Global instance ───────────────────────────────────────────────────────────
window.wsManager = new WSManager();
