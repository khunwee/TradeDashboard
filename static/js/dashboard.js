// =============================================================================
// dashboard.js — Main Dashboard Logic (Alpine.js)
// API calls, state management, real-time updates, routing
// =============================================================================

const API_BASE = '/api/v1';

// ── HTTP Client with auto token refresh ──────────────────────────────────────
const api = {
  async _fetch(url, opts = {}) {
    const token = localStorage.getItem('access_token');
    const headers = {
      'Content-Type': 'application/json',
      ...(token ? { Authorization: `Bearer ${token}` } : {}),
      ...(opts.headers || {}),
    };

    let res = await fetch(`${API_BASE}${url}`, { ...opts, headers });

    // Auto-refresh on 401
    if (res.status === 401) {
      const refreshed = await this._refreshToken();
      if (refreshed) {
        headers.Authorization = `Bearer ${localStorage.getItem('access_token')}`;
        res = await fetch(`${API_BASE}${url}`, { ...opts, headers });
      } else {
        window.location.href = '/static/login.html';
        throw new Error('Unauthorized');
      }
    }

    if (!res.ok) {
      const err = await res.json().catch(() => ({ detail: res.statusText }));
      throw new Error(err.detail || `HTTP ${res.status}`);
    }
    return res.json();
  },

  _refreshing: false,

  async _refreshToken() {
    // Prevent concurrent refresh attempts
    if (this._refreshing) return false;
    this._refreshing = true;

    const rt = localStorage.getItem('refresh_token');
    if (!rt) {
      this._refreshing = false;
      // No refresh token — clear everything and go to login
      localStorage.clear();
      window.location.href = '/static/login.html';
      return false;
    }
    try {
      const res = await fetch(`${API_BASE}/auth/refresh`, {
        method:  'POST',
        headers: { 'Content-Type': 'application/json' },
        body:    JSON.stringify({ refresh_token: rt }),
      });
      if (!res.ok) {
        // Refresh failed — tokens are invalid, clear and redirect
        localStorage.clear();
        window.location.href = '/static/login.html';
        return false;
      }
      const data = await res.json();
      localStorage.setItem('access_token',  data.access_token);
      localStorage.setItem('refresh_token', data.refresh_token);
      return true;
    } catch {
      return false;
    } finally {
      this._refreshing = false;
    }
  },

  get:    (url, opts)          => api._fetch(url, { method: 'GET', ...opts }),
  post:   (url, body, opts)    => api._fetch(url, { method: 'POST',   body: JSON.stringify(body), ...opts }),
  patch:  (url, body, opts)    => api._fetch(url, { method: 'PATCH',  body: JSON.stringify(body), ...opts }),
  delete: (url, opts)          => api._fetch(url, { method: 'DELETE', ...opts }),
};


// ── Toast Notification Manager ────────────────────────────────────────────────
const toast = {
  _container: null,

  init() {
    this._container = document.getElementById('toastContainer');
    if (!this._container) {
      this._container = document.createElement('div');
      this._container.id = 'toastContainer';
      this._container.className = 'toast-container';
      document.body.appendChild(this._container);
    }
  },

  show(message, type = 'info', duration = 4000) {
    if (!this._container) this.init();

    const icons = {
      info:    '→',
      success: '✓',
      error:   '✕',
      warning: '⚠',
    };

    const el = document.createElement('div');
    el.className = `toast ${type}`;
    el.innerHTML = `
      <span style="font-size:16px;line-height:1;color:var(--${type === 'success' ? 'green' : type === 'error' ? 'red' : 'accent'})">${icons[type] || '→'}</span>
      <div style="flex:1">
        <div style="font-size:13px;color:var(--text-primary);font-weight:500">${message}</div>
      </div>
      <button onclick="this.parentElement.remove()" style="background:none;border:none;color:var(--text-muted);cursor:pointer;font-size:16px;line-height:1;padding:0">×</button>
    `;
    this._container.appendChild(el);

    if (duration > 0) {
      setTimeout(() => {
        el.style.opacity = '0';
        el.style.transform = 'translateX(20px)';
        el.style.transition = 'all 0.3s ease';
        setTimeout(() => el.remove(), 300);
      }, duration);
    }
  },

  success: (msg, d) => toast.show(msg, 'success', d),
  error:   (msg, d) => toast.show(msg, 'error', d),
  info:    (msg, d) => toast.show(msg, 'info', d),
  warning: (msg, d) => toast.show(msg, 'warning', d),
};


// =============================================================================
// ALPINE.JS COMPONENTS
// =============================================================================

// ── Main App Store ────────────────────────────────────────────────────────────
document.addEventListener('alpine:init', () => {

  Alpine.store('app', {
    user:     JSON.parse(localStorage.getItem('user') || 'null'),
    theme:    localStorage.getItem('theme') || 'dark',
    sidebarOpen: true,

    async logout() {
      const rt = localStorage.getItem('refresh_token');
      if (rt) await api.post('/auth/logout', { refresh_token: rt }).catch(() => {});
      localStorage.clear();
      window.location.href = '/static/login.html';
    },
  });


  // ── Dashboard Component ─────────────────────────────────────────────────────
  Alpine.data('dashboard', () => ({
    accounts:       [],
    selectedId:     null,
    portfolio:      null,
    loading:        true,
    notifications:  [],
    unreadCount:    0,
    notifOpen:      false,
    activePage:     'portfolio',
    searchQuery:    '',
    filterStatus:   '',

    async init() {
      if (!localStorage.getItem('access_token')) {
        window.location.href = '/static/login.html';
        return;
      }
      toast.init();
      await this.loadAccounts();
      await this.loadNotifications();
      this.connectPortfolioWS();
    },

    async loadAccounts() {
      this.loading = true;
      try {
        const data = await api.get('/accounts');
        this.accounts = data.accounts;
        this.portfolio = data.summary;
      } catch (e) {
        toast.error('Failed to load accounts: ' + e.message);
      } finally {
        this.loading = false;
      }
    },

    get filteredAccounts() {
      let list = this.accounts;
      if (this.searchQuery) {
        const q = this.searchQuery.toLowerCase();
        list = list.filter(a =>
          (a.label || '').toLowerCase().includes(q) ||
          a.account_number.toLowerCase().includes(q) ||
          (a.ea_name || '').toLowerCase().includes(q)
        );
      }
      if (this.filterStatus) {
        list = list.filter(a => a.status === this.filterStatus);
      }
      return list;
    },

    selectAccount(id) {
      this.selectedId  = id;
      this.activePage  = 'account';
    },

    showPortfolio() {
      this.selectedId = null;
      this.activePage = 'portfolio';
    },

    connectPortfolioWS() {
      wsManager.connectPortfolio((data) => {
        if (data.type === 'live_update') {
          const acct = this.accounts.find(a => a.id === data.account_id);
          if (acct) {
            acct.equity      = data.equity;
            acct.balance     = data.balance;
            acct.floating_pl = data.floating_pl;
            acct.margin_level = data.margin_level;
            acct.open_orders_count = data.open_orders;
          }
          this.recalcPortfolio();
        }
      });
    },

    recalcPortfolio() {
      this.portfolio = {
        total_balance:      this.accounts.reduce((s, a) => s + a.balance, 0),
        total_equity:       this.accounts.reduce((s, a) => s + a.equity, 0),
        total_floating_pl:  this.accounts.reduce((s, a) => s + a.floating_pl, 0),
        total_profit_today: this.accounts.reduce((s, a) => s + a.profit_today, 0),
        total_open_orders:  this.accounts.reduce((s, a) => s + a.open_orders_count, 0),
      };
    },

    async loadNotifications() {
      try {
        const data = await api.get('/alerts/notifications?unread_only=true&limit=20');
        this.notifications = data.notifications;
        this.unreadCount   = data.unread_count;
      } catch { }
    },

    async markAllRead() {
      await api.post('/alerts/notifications/read-all', {});
      this.unreadCount = 0;
      this.notifications.forEach(n => n.is_read = true);
    },

    fmt: Charts.fmt,

    plClass: (v) => v >= 0 ? 'profit' : 'loss',
    plSign:  (v) => v >= 0 ? `+$${Math.abs(v).toLocaleString('en-US', {minimumFractionDigits:2,maximumFractionDigits:2})}` : `-$${Math.abs(v).toLocaleString('en-US', {minimumFractionDigits:2,maximumFractionDigits:2})}`,
    statusClass: (s) => s === 'live' ? 'profit' : s === 'delayed' ? '' : 'loss',
  }));


  // ── Account Detail Component ────────────────────────────────────────────────
  Alpine.data('accountDetail', (accountId) => ({
    account:     null,
    trades:      [],
    openPositions: [],
    activeTab:   'overview',
    chartPeriod: '1M',
    loading:     true,
    tradesPage:  1,
    tradePages:  1,
    tradeTotal:  0,
    chartInstance: null,
    wsKey:       null,

    async init() {
      await this.loadAccount();
      await this.loadOpenPositions();
      this.wsKey = wsManager.connectAccount(accountId, (data) => {
        if (data.type === 'live_update' && data.account_id === accountId) {
          this.account.equity       = data.equity;
          this.account.balance      = data.balance;
          this.account.floating_pl  = data.floating_pl;
          this.account.margin_level = data.margin_level;
          this.account.open_orders_count = data.open_orders;
        }
      });
      await this.loadEquityCurve();
    },

    async loadAccount() {
      this.loading = true;
      try {
        this.account = await api.get(`/accounts/${accountId}`);
      } catch (e) {
        toast.error('Failed to load account');
      } finally {
        this.loading = false;
      }
    },

    async loadEquityCurve() {
      try {
        const data = await api.get(`/stats/${accountId}/equity-curve?period=${this.chartPeriod}`);
        this.$nextTick(() => {
          Charts.renderEquityCurve('equityChart', data.data, { height: 300, markers: data.markers });
          Charts.renderDrawdownChart('drawdownChart', data.data);
        });
      } catch (e) {
        console.error('Equity chart error:', e);
      }
    },

    async switchPeriod(period) {
      this.chartPeriod = period;
      await this.loadEquityCurve();
    },

    async loadOpenPositions() {
      try {
        const data = await api.get(`/trades/${accountId}/open`);
        this.openPositions = data.positions;
      } catch { }
    },

    async loadTrades(page = 1) {
      try {
        const data = await api.get(`/trades/${accountId}/closed?page=${page}&page_size=50`);
        this.trades     = data.trades;
        this.tradePages = data.pagination.pages;
        this.tradeTotal = data.pagination.total;
        this.tradesPage = page;
      } catch (e) {
        toast.error('Failed to load trades');
      }
    },

    async switchTab(tab) {
      this.activeTab = tab;

      this.$nextTick(async () => {
        if (tab === 'trades' && !this.trades.length) {
          await this.loadTrades();
        }
        if (tab === 'analytics') {
          await this.loadAnalytics();
        }
        if (tab === 'risk') {
          await this.loadRisk();
        }
      });
    },

    async loadAnalytics() {
      try {
        const [symbols, direction, monthly, hourly, distribution, duration] = await Promise.all([
          api.get(`/stats/${accountId}/symbols`),
          api.get(`/stats/${accountId}/direction`),
          api.get(`/stats/${accountId}/heatmap/monthly`),
          api.get(`/stats/${accountId}/heatmap/hourly`),
          api.get(`/stats/${accountId}/distribution/profit`),
          api.get(`/stats/${accountId}/distribution/duration`),
        ]);

        this.$nextTick(() => {
          Charts.renderSymbolChart('symbolChart', symbols);
          Charts.renderMonthlyHeatmap('monthlyHeatmap', monthly);
          Charts.renderHourlyHeatmap('hourlyHeatmap', hourly);
          Charts.renderProfitDistribution('profitDistChart', distribution);
          Charts.renderDurationChart('durationChart', duration);
        });
      } catch (e) {
        toast.error('Failed to load analytics');
      }
    },

    async loadRisk() {
      try {
        const [exposure, rolling, dailyPL] = await Promise.all([
          api.get(`/stats/${accountId}/currency-exposure`),
          api.get(`/stats/${accountId}/rolling?window=30`),
          api.get(`/stats/${accountId}/daily-pl?days=90`),
        ]);

        this.$nextTick(() => {
          Charts.renderCurrencyExposure('currencyChart', exposure);
          Charts.renderRollingMetrics('rollingChart', rolling);
          Charts.renderDailyPL('dailyPLChart', dailyPL);
        });
      } catch { }
    },

    async exportCSV() {
      const token = localStorage.getItem('access_token');
      window.open(`${API_BASE}/trades/${accountId}/export/csv?token=${token}`, '_blank');
    },

    destroy() {
      if (this.wsKey) wsManager.disconnect(this.wsKey);
    },

    // Helpers
    fmt:       Charts.fmt,
    plClass:   (v) => v > 0 ? 'profit' : v < 0 ? 'loss' : '',
    plSign:    (v) => v >= 0 ? `+$${Math.abs(v).toFixed(2)}` : `-$${Math.abs(v).toFixed(2)}`,
    duration:  (min) => {
      if (!min) return '—';
      if (min < 60) return `${min.toFixed(0)}m`;
      if (min < 1440) return `${(min/60).toFixed(1)}h`;
      return `${(min/1440).toFixed(1)}d`;
    },
  }));


  // ── Alerts Manager Component ───────────────────────────────────────────────
  Alpine.data('alertsManager', () => ({
    rules:       [],
    conditions:  [],
    channels:    [],
    showForm:    false,
    editRule:    null,
    form: {
      label: '', account_id: null, condition_type: '', threshold_value: null,
      threshold_unit: 'dollar', channels: ['in_app'], cooldown_min: 15,
    },

    async init() {
      await Promise.all([
        this.loadRules(),
        this.loadConditions(),
        this.loadChannels(),
      ]);
    },

    async loadRules() {
      this.rules = await api.get('/alerts/rules');
    },

    async loadConditions() {
      this.conditions = await api.get('/alerts/conditions');
    },

    async loadChannels() {
      this.channels = await api.get('/alerts/channels');
    },

    async saveRule() {
      try {
        if (this.editRule) {
          await api.patch(`/alerts/rules/${this.editRule.id}`, this.form);
          toast.success('Alert rule updated');
        } else {
          await api.post('/alerts/rules', this.form);
          toast.success('Alert rule created');
        }
        this.showForm = false;
        this.editRule = null;
        await this.loadRules();
      } catch (e) {
        toast.error('Failed to save rule: ' + e.message);
      }
    },

    async toggleRule(rule) {
      await api.patch(`/alerts/rules/${rule.id}`, { is_active: !rule.is_active });
      rule.is_active = !rule.is_active;
    },

    async deleteRule(rule) {
      if (!confirm(`Delete alert rule "${rule.label}"?`)) return;
      await api.delete(`/alerts/rules/${rule.id}`);
      this.rules = this.rules.filter(r => r.id !== rule.id);
      toast.success('Rule deleted');
    },

    editForm(rule) {
      this.editRule = rule;
      this.form = { ...rule };
      this.showForm = true;
    },

    toggleChannel(ch) {
      const idx = this.form.channels.indexOf(ch);
      idx >= 0 ? this.form.channels.splice(idx, 1) : this.form.channels.push(ch);
    },
  }));


  // ── Account Settings Component ─────────────────────────────────────────────
  Alpine.data('accountSettings', (accountId) => ({
    account: null,
    apiKey:  null,
    form:    {},
    deposits: [],
    showDepositForm: false,
    depositForm: { amount: '', note: '', tx_date: new Date().toISOString().slice(0,10), type: 'deposit' },

    async init() {
      this.account = await api.get(`/accounts/${accountId}`);
      this.form    = {
        label: this.account.label || '',
        broker_name: this.account.broker_name || '',
        heartbeat_timeout_sec: this.account.heartbeat_timeout_sec || 60,
        push_interval_sec: this.account.push_interval_sec || 5,
      };
      this.deposits = await api.get(`/accounts/${accountId}/deposits`);
    },

    async saveSettings() {
      try {
        await api.patch(`/accounts/${accountId}`, this.form);
        toast.success('Settings saved');
      } catch (e) {
        toast.error('Save failed: ' + e.message);
      }
    },

    async generateApiKey() {
      if (!confirm('Generate a new API key? This will invalidate the existing key.')) return;
      const data = await api.post(`/accounts/${accountId}/api-key`, {});
      this.apiKey = data.api_key;
      toast.success('New API key generated — copy it now!', 0);
    },

    async revokeApiKey() {
      if (!confirm('Revoke EA API key? The EA will stop pushing data.')) return;
      await api.delete(`/accounts/${accountId}/api-key`);
      this.apiKey = null;
      toast.info('API key revoked');
    },

    async addDeposit() {
      const amount = parseFloat(this.depositForm.amount);
      if (!amount) return;
      const entry = {
        amount: this.depositForm.type === 'withdrawal' ? -Math.abs(amount) : Math.abs(amount),
        note:   this.depositForm.note,
        tx_date: this.depositForm.tx_date,
      };
      await api.post(`/accounts/${accountId}/deposits`, entry);
      this.deposits = await api.get(`/accounts/${accountId}/deposits`);
      this.showDepositForm = false;
      toast.success('Transaction recorded');
    },

    copyToClipboard(text) {
      navigator.clipboard.writeText(text);
      toast.success('Copied to clipboard');
    },
  }));

  // ── Add Account Form Component ─────────────────────────────────────────────
  Alpine.data('addAccountForm', () => ({
    loading:    false,
    successMsg: '',
    errorMsg:   '',
    apiKey:     null,
    form: {
      account_number:   '',
      broker_server:    '',
      broker_name:      '',
      label:            '',
      account_currency: 'USD',
      account_type:     'live',
      leverage:         100,
      initial_deposit:  0,
    },

    init() {
      this.reset();
    },

    reset() {
      this.form = {
        account_number: '', broker_server: '', broker_name: '',
        label: '', account_currency: 'USD', account_type: 'live',
        leverage: 100, initial_deposit: 0,
      };
      this.successMsg = '';
      this.errorMsg   = '';
      this.apiKey     = null;
      this.loading    = false;
    },

    async submit() {
      this.errorMsg   = '';
      this.successMsg = '';

      if (!this.form.account_number.trim()) {
        this.errorMsg = 'Account number is required'; return;
      }
      if (!this.form.broker_server.trim()) {
        this.errorMsg = 'Broker server is required'; return;
      }

      this.loading = true;
      try {
        // Create the account
        const account = await api.post('/accounts', this.form);

        // Auto-generate API key
        try {
          const keyData = await api.post(`/accounts/${account.id}/api-key`, {});
          this.apiKey = keyData.api_key;
        } catch(e) {
          console.warn('API key generation failed:', e);
        }

        this.successMsg = `Account "${account.label || account.account_number}" created successfully!`;

        // Reload accounts list in sidebar
        if (window._dashboardApp) {
          await window._dashboardApp.loadAccounts();
        }

      } catch(e) {
        this.errorMsg = e.message || 'Failed to create account';
      } finally {
        this.loading = false;
      }
    },

    copyKey() {
      if (this.apiKey) {
        navigator.clipboard.writeText(this.apiKey);
        toast.success('API key copied! Paste it into MetaTrader EA settings.');
      }
    },
  }));

}); // end alpine:init


// ── Format helpers for use in templates ──────────────────────────────────────
window.fmtCurrency = (v) => `$${Math.abs(v || 0).toLocaleString('en-US', {minimumFractionDigits:2,maximumFractionDigits:2})}`;
window.fmtPct      = (v) => `${(v || 0) >= 0 ? '+' : ''}${(v || 0).toFixed(2)}%`;
window.fmtPL       = (v) => `${(v || 0) >= 0 ? '+$' : '-$'}${Math.abs(v || 0).toLocaleString('en-US', {minimumFractionDigits:2,maximumFractionDigits:2})}`;
