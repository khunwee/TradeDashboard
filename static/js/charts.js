// =============================================================================
// charts.js — All Chart Rendering (Chart.js + Lightweight Charts)
// Equity Curve, Daily P/L, Heatmaps, Monthly Returns, Distributions
// =============================================================================

// ── Shared Color Palette ──────────────────────────────────────────────────────
const C = {
  accent:    '#00D4AA',
  accentDim: '#00A882',
  green:     '#22C55E',
  red:       '#EF4444',
  yellow:    '#F59E0B',
  blue:      '#3B82F6',
  purple:    '#8B5CF6',
  border:    '#222222',
  text:      '#A0A0A0',
  textLight: '#F0F0F0',
  bg:        '#131313',
  bgAlt:     '#0E0E0E',
  grid:      'rgba(255,255,255,0.04)',
};

// ── Chart.js Global Defaults ──────────────────────────────────────────────────
if (window.Chart) {
  Chart.defaults.color             = C.text;
  Chart.defaults.borderColor       = C.border;
  Chart.defaults.font.family       = "'DM Sans', sans-serif";
  Chart.defaults.font.size         = 12;
  Chart.defaults.plugins.legend.display = false;
  Chart.defaults.plugins.tooltip.backgroundColor = '#1C1C1C';
  Chart.defaults.plugins.tooltip.borderColor      = C.border;
  Chart.defaults.plugins.tooltip.borderWidth      = 1;
  Chart.defaults.plugins.tooltip.padding          = 10;
  Chart.defaults.plugins.tooltip.cornerRadius     = 8;
  Chart.defaults.plugins.tooltip.titleFont.weight = '600';
}

// ── Utility ───────────────────────────────────────────────────────────────────
const fmt = {
  currency: (v, sym='$') => `${sym}${Math.abs(v).toLocaleString('en-US',{minimumFractionDigits:2,maximumFractionDigits:2})}`,
  pct:      (v)          => `${v >= 0 ? '+' : ''}${v.toFixed(2)}%`,
  num:      (v)          => v.toLocaleString('en-US'),
  pl:       (v)          => `${v >= 0 ? '+' : ''}${fmt.currency(v)}`,
};

function destroyChart(canvasId) {
  const existing = Chart.getChart(canvasId);
  if (existing) existing.destroy();
}


// =============================================================================
// 1. EQUITY CURVE (Lightweight Charts — TradingView style)
// =============================================================================

function renderEquityCurve(containerId, data, options = {}) {
  const container = document.getElementById(containerId);
  if (!container) return;
  container.innerHTML = '';

  // If Lightweight Charts not loaded, fall back to Chart.js
  if (!window.LightweightCharts) {
    return renderEquityCurveChartJS(containerId, data, options);
  }

  const chart = LightweightCharts.createChart(container, {
    width:  container.clientWidth,
    height: options.height || 320,
    layout: {
      background: { color: 'transparent' },
      textColor:  C.text,
      fontFamily: 'IBM Plex Mono',
      fontSize:   11,
    },
    grid: {
      vertLines:   { color: C.grid },
      horzLines:   { color: C.grid },
    },
    crosshair: {
      mode: LightweightCharts.CrosshairMode.Normal,
    },
    rightPriceScale: {
      borderColor: C.border,
      scaleMargins: { top: 0.1, bottom: 0.1 },
    },
    timeScale: {
      borderColor: C.border,
      timeVisible: true,
      secondsVisible: false,
    },
    handleScroll: true,
    handleScale:  true,
  });

  // Equity line
  const equitySeries = chart.addAreaSeries({
    lineColor:    C.accent,
    topColor:     `${C.accent}30`,
    bottomColor:  `${C.accent}02`,
    lineWidth:    2,
    crosshairMarkerVisible: true,
  });

  // Balance line
  const balanceSeries = chart.addLineSeries({
    color:     '#6B7280',
    lineWidth: 1,
    lineStyle: LightweightCharts.LineStyle.Dashed,
    crosshairMarkerVisible: false,
  });

  const equityData  = [];
  const balanceData = [];

  data.forEach(point => {
    const time = Math.floor(new Date(point.ts).getTime() / 1000);
    equityData.push({ time, value: point.equity });
    balanceData.push({ time, value: point.balance });
  });

  if (equityData.length > 0) {
    equitySeries.setData(equityData);
    balanceSeries.setData(balanceData);
  }

  // Add deposit/withdrawal markers
  if (options.markers?.deposits?.length) {
    const markers = options.markers.deposits.map(d => ({
      time:     Math.floor(new Date(d.ts).getTime() / 1000),
      position: d.amount > 0 ? 'belowBar' : 'aboveBar',
      color:    d.amount > 0 ? C.green : C.red,
      shape:    d.amount > 0 ? 'arrowUp' : 'arrowDown',
      text:     `${d.amount > 0 ? '+' : ''}$${Math.abs(d.amount).toLocaleString()}`,
      size:     1,
    }));
    equitySeries.setMarkers(markers.sort((a,b) => a.time - b.time));
  }

  // Resize observer
  const ro = new ResizeObserver(() => {
    chart.resize(container.clientWidth, options.height || 320);
  });
  ro.observe(container);

  return chart;
}

function renderEquityCurveChartJS(containerId, data, options = {}) {
  destroyChart(containerId);
  const ctx = document.getElementById(containerId);
  if (!ctx) return;

  const labels   = data.map(d => new Date(d.ts).toLocaleDateString());
  const equities = data.map(d => d.equity);
  const balances = data.map(d => d.balance);

  return new Chart(ctx, {
    type: 'line',
    data: {
      labels,
      datasets: [
        {
          label: 'Equity',
          data: equities,
          borderColor: C.accent,
          backgroundColor: `${C.accent}20`,
          borderWidth: 2,
          fill: true,
          tension: 0.3,
          pointRadius: 0,
        },
        {
          label: 'Balance',
          data: balances,
          borderColor: '#6B7280',
          borderWidth: 1,
          borderDash: [4, 4],
          fill: false,
          tension: 0.3,
          pointRadius: 0,
        },
      ],
    },
    options: {
      responsive: true,
      maintainAspectRatio: false,
      animation: false,
      scales: {
        x: { display: false },
        y: {
          grid: { color: C.grid },
          ticks: { callback: v => `$${(v/1000).toFixed(0)}k` },
        },
      },
      plugins: {
        tooltip: {
          mode: 'index',
          intersect: false,
          callbacks: {
            label: ctx => `${ctx.dataset.label}: ${fmt.currency(ctx.parsed.y)}`,
          },
        },
      },
    },
  });
}


// =============================================================================
// 2. DAILY P/L BAR CHART
// =============================================================================

function renderDailyPL(canvasId, data) {
  destroyChart(canvasId);
  const ctx = document.getElementById(canvasId);
  if (!ctx || !data?.length) return;

  const labels  = data.map(d => d.date.slice(5)); // MM-DD
  const values  = data.map(d => d.pl);
  const colors  = values.map(v => v >= 0 ? `${C.green}CC` : `${C.red}CC`);
  const borders = values.map(v => v >= 0 ? C.green : C.red);

  return new Chart(ctx, {
    type: 'bar',
    data: {
      labels,
      datasets: [{
        label: 'Daily P/L',
        data: values,
        backgroundColor: colors,
        borderColor: borders,
        borderWidth: 1,
        borderRadius: 3,
      }],
    },
    options: {
      responsive: true,
      maintainAspectRatio: false,
      animation: { duration: 300 },
      scales: {
        x: {
          grid: { display: false },
          ticks: { maxTicksLimit: 10, font: { size: 10 } },
        },
        y: {
          grid: { color: C.grid },
          ticks: {
            callback: v => `$${v >= 0 ? '' : '-'}${Math.abs(v).toLocaleString()}`,
            font: { family: 'IBM Plex Mono', size: 10 },
          },
        },
      },
      plugins: {
        tooltip: {
          callbacks: {
            label: ctx => {
              const d = data[ctx.dataIndex];
              return [`P/L: ${fmt.pl(ctx.parsed.y)}`, `Return: ${fmt.pct(d.pl_pct)}`, `Trades: ${d.trade_count}`];
            },
          },
        },
      },
    },
  });
}


// =============================================================================
// 3. PROFIT DISTRIBUTION HISTOGRAM
// =============================================================================

function renderProfitDistribution(canvasId, data) {
  destroyChart(canvasId);
  const ctx = document.getElementById(canvasId);
  if (!ctx || !data?.length) return;

  const labels = data.map(d => `$${d.from.toFixed(0)}`);
  const wins   = data.map(d => d.wins);
  const losses = data.map(d => d.losses);

  return new Chart(ctx, {
    type: 'bar',
    data: {
      labels,
      datasets: [
        { label: 'Wins',   data: wins,   backgroundColor: `${C.green}BB`, borderRadius: 2 },
        { label: 'Losses', data: losses, backgroundColor: `${C.red}BB`,   borderRadius: 2 },
      ],
    },
    options: {
      responsive: true,
      maintainAspectRatio: false,
      animation: { duration: 300 },
      plugins: {
        legend: { display: true, position: 'top', labels: { boxWidth: 10, font: { size: 11 } } },
      },
      scales: {
        x: { grid: { display: false }, stacked: false, ticks: { font: { size: 10 } } },
        y: { grid: { color: C.grid } },
      },
    },
  });
}


// =============================================================================
// 4. MONTHLY RETURNS HEATMAP (canvas-based)
// =============================================================================

function renderMonthlyHeatmap(containerId, data) {
  const container = document.getElementById(containerId);
  if (!container || !data?.length) return;

  const months   = ['Jan','Feb','Mar','Apr','May','Jun','Jul','Aug','Sep','Oct','Nov','Dec'];
  const byYear   = {};
  data.forEach(d => {
    if (!byYear[d.year]) byYear[d.year] = {};
    byYear[d.year][d.month - 1] = d.pct;
  });

  const years = Object.keys(byYear).sort();
  const maxAbs = Math.max(...data.map(d => Math.abs(d.pct)), 1);

  function pctToColor(pct) {
    if (pct === undefined) return '#1A1A1A';
    const intensity = Math.min(Math.abs(pct) / maxAbs, 1);
    if (pct >= 0) {
      const r = Math.round(34  * (1 - intensity * 0.3));
      const g = Math.round(197 * intensity + 30 * (1 - intensity));
      const b = Math.round(94  * (1 - intensity * 0.5));
      return `rgb(${r},${g},${b})`;
    } else {
      const r = Math.round(239 * intensity + 30 * (1 - intensity));
      const g = Math.round(68  * (1 - intensity * 0.8));
      const b = Math.round(68  * (1 - intensity * 0.6));
      return `rgb(${r},${g},${b})`;
    }
  }

  let html = `<div style="overflow-x:auto">
    <table style="border-collapse:separate;border-spacing:3px;font-size:11px;">
      <thead><tr>
        <th style="color:var(--text-muted);text-align:left;padding:3px 8px;font-weight:600;font-size:10px"></th>
        ${months.map(m => `<th style="color:var(--text-muted);text-align:center;padding:3px 5px;font-weight:600;font-size:10px;min-width:44px">${m}</th>`).join('')}
      </tr></thead>
      <tbody>`;

  years.forEach(year => {
    html += `<tr><td style="color:var(--text-muted);padding:3px 8px;font-size:10px;font-weight:600">${year}</td>`;
    for (let m = 0; m < 12; m++) {
      const pct  = byYear[year][m];
      const bg   = pctToColor(pct);
      const text = pct !== undefined ? (pct >= 0 ? `+${pct.toFixed(1)}` : pct.toFixed(1)) + '%' : '';
      const fg   = pct !== undefined && Math.abs(pct) > maxAbs * 0.6 ? '#fff' : '#ccc';
      html += `<td title="${months[m]} ${year}: ${text}" style="background:${bg};color:${fg};text-align:center;padding:6px 2px;border-radius:4px;cursor:pointer;font-family:'IBM Plex Mono';font-size:10px;transition:transform 0.1s" onmouseover="this.style.transform='scale(1.1)'" onmouseout="this.style.transform=''">${text}</td>`;
    }
    html += '</tr>';
  });

  html += '</tbody></table></div>';
  container.innerHTML = html;
}


// =============================================================================
// 5. HOURLY HEATMAP (0-23 hours × Mon-Fri)
// =============================================================================

function renderHourlyHeatmap(containerId, data) {
  const container = document.getElementById(containerId);
  if (!container || !data?.length) return;

  const days = ['Mon','Tue','Wed','Thu','Fri','Sat','Sun'];
  const hours = Array.from({length: 24}, (_, i) => `${String(i).padStart(2,'0')}:00`);

  // Build lookup
  const map = {};
  let maxAbs = 0.01;
  data.forEach(d => {
    map[`${d.weekday}_${d.hour}`] = d;
    maxAbs = Math.max(maxAbs, Math.abs(d.avg_profit));
  });

  const cellSize = 26;
  const padLeft  = 36;
  const padTop   = 28;

  const W = padLeft + 24 * cellSize;
  const H = padTop  +  7 * cellSize;

  const canvas = document.createElement('canvas');
  canvas.width  = W;
  canvas.height = H;
  canvas.style.cssText = 'max-width:100%;height:auto;';
  container.innerHTML = '';
  container.appendChild(canvas);

  const ctx = canvas.getContext('2d');
  ctx.font = '10px IBM Plex Mono';
  ctx.fillStyle = '#606060';
  ctx.textAlign = 'center';

  // Hour labels
  for (let h = 0; h < 24; h += 3) {
    ctx.fillText(`${h}h`, padLeft + h * cellSize + cellSize / 2, 14);
  }

  // Day labels
  ctx.textAlign = 'right';
  days.forEach((d, i) => {
    ctx.fillText(d, padLeft - 4, padTop + i * cellSize + cellSize / 2 + 4);
  });

  // Cells
  for (let day = 0; day < 7; day++) {
    for (let hour = 0; hour < 24; hour++) {
      const cell = map[`${day}_${hour}`];
      const x = padLeft + hour * cellSize;
      const y = padTop  + day  * cellSize;

      if (cell) {
        const intensity = Math.min(Math.abs(cell.avg_profit) / maxAbs, 1);
        const r = cell.avg_profit >= 0 ? Math.round(34 + intensity * 50)  : Math.round(239);
        const g = cell.avg_profit >= 0 ? Math.round(197)                   : Math.round(68 * (1 - intensity * 0.5));
        const b = cell.avg_profit >= 0 ? Math.round(94)                    : Math.round(68);
        const a = 0.15 + intensity * 0.75;
        ctx.fillStyle = `rgba(${r},${g},${b},${a})`;
      } else {
        ctx.fillStyle = 'rgba(30,30,30,0.6)';
      }
      roundRect(ctx, x + 1, y + 1, cellSize - 2, cellSize - 2, 3);
      ctx.fill();

      if (cell && cell.trade_count > 0) {
        ctx.fillStyle = 'rgba(255,255,255,0.5)';
        ctx.font = '8px IBM Plex Mono';
        ctx.textAlign = 'center';
        ctx.fillText(cell.trade_count, x + cellSize / 2, y + cellSize / 2 + 3);
      }
    }
  }
}

function roundRect(ctx, x, y, w, h, r) {
  ctx.beginPath();
  ctx.moveTo(x + r, y);
  ctx.arcTo(x + w, y, x + w, y + h, r);
  ctx.arcTo(x + w, y + h, x, y + h, r);
  ctx.arcTo(x, y + h, x, y, r);
  ctx.arcTo(x, y, x + w, y, r);
  ctx.closePath();
}


// =============================================================================
// 6. CURRENCY EXPOSURE BAR CHART
// =============================================================================

function renderCurrencyExposure(canvasId, data) {
  destroyChart(canvasId);
  const ctx = document.getElementById(canvasId);
  if (!ctx) return;

  const currencies = Object.keys(data);
  const values     = Object.values(data);
  const colors     = values.map(v => v > 0 ? `${C.green}BB` : `${C.red}BB`);

  return new Chart(ctx, {
    type: 'bar',
    data: {
      labels: currencies,
      datasets: [{
        label: 'Net Exposure (lots)',
        data: values,
        backgroundColor: colors,
        borderRadius: 4,
      }],
    },
    options: {
      responsive: true,
      maintainAspectRatio: false,
      indexAxis: 'y',
      animation: { duration: 300 },
      plugins: {
        tooltip: {
          callbacks: {
            label: ctx => `${ctx.parsed.x > 0 ? 'Long' : 'Short'}: ${Math.abs(ctx.parsed.x).toFixed(2)} lots`,
          },
        },
      },
      scales: {
        x: {
          grid: { color: C.grid },
          ticks: { font: { family: 'IBM Plex Mono', size: 11 } },
        },
        y: {
          grid: { display: false },
          ticks: { font: { family: 'IBM Plex Mono', size: 11, weight: '600' } },
        },
      },
    },
  });
}


// =============================================================================
// 7. ROLLING METRICS LINE CHART
// =============================================================================

function renderRollingMetrics(canvasId, data) {
  destroyChart(canvasId);
  const ctx = document.getElementById(canvasId);
  if (!ctx || !data?.length) return;

  const labels      = data.map(d => d.date.slice(0, 10));
  const sharpe      = data.map(d => d.sharpe);
  const pf          = data.map(d => d.profit_factor);
  const wr          = data.map(d => d.win_rate);

  return new Chart(ctx, {
    type: 'line',
    data: {
      labels,
      datasets: [
        {
          label: 'Sharpe', data: sharpe, borderColor: C.accent,
          borderWidth: 2, pointRadius: 0, tension: 0.4, yAxisID: 'y1',
        },
        {
          label: 'Profit Factor', data: pf, borderColor: C.blue,
          borderWidth: 1.5, pointRadius: 0, tension: 0.4, yAxisID: 'y2',
        },
        {
          label: 'Win Rate %', data: wr, borderColor: C.yellow,
          borderWidth: 1.5, pointRadius: 0, tension: 0.4, yAxisID: 'y3', borderDash: [4,4],
        },
      ],
    },
    options: {
      responsive: true,
      maintainAspectRatio: false,
      animation: false,
      plugins: {
        legend: { display: true, position: 'top', labels: { boxWidth: 10, font: { size: 11 } } },
      },
      scales: {
        x:  { display: false },
        y1: { grid: { color: C.grid }, position: 'left',  title: { display: true, text: 'Sharpe', font: { size: 10 } } },
        y2: { display: false },
        y3: { display: false },
      },
    },
  });
}


// =============================================================================
// 8. DRAWDOWN CHART (overlaid on equity)
// =============================================================================

function renderDrawdownChart(canvasId, equityData) {
  destroyChart(canvasId);
  const ctx = document.getElementById(canvasId);
  if (!ctx || !equityData?.length) return;

  const equities = equityData.map(d => d.equity);
  const labels   = equityData.map(d => d.ts.slice(0, 10));

  let peak = equities[0] || 0;
  const drawdowns = equities.map(eq => {
    peak = Math.max(peak, eq);
    return peak > 0 ? ((eq - peak) / peak * 100) : 0;
  });

  return new Chart(ctx, {
    type: 'line',
    data: {
      labels,
      datasets: [{
        label: 'Drawdown %',
        data: drawdowns,
        borderColor: `${C.red}CC`,
        backgroundColor: `${C.red}25`,
        fill: true,
        borderWidth: 1.5,
        pointRadius: 0,
        tension: 0.2,
      }],
    },
    options: {
      responsive: true,
      maintainAspectRatio: false,
      animation: false,
      scales: {
        x: { display: false },
        y: {
          grid: { color: C.grid },
          ticks: { callback: v => `${v.toFixed(1)}%`, font: { family: 'IBM Plex Mono', size: 10 } },
          reverse: false,
          suggestedMax: 0,
        },
      },
      plugins: {
        tooltip: {
          callbacks: {
            label: ctx => `Drawdown: ${ctx.parsed.y.toFixed(2)}%`,
          },
        },
      },
    },
  });
}


// =============================================================================
// 9. SYMBOL PERFORMANCE (horizontal bar)
// =============================================================================

function renderSymbolChart(canvasId, data, metric = 'net_profit') {
  destroyChart(canvasId);
  const ctx = document.getElementById(canvasId);
  if (!ctx || !data?.length) return;

  const sorted = [...data].sort((a, b) => b[metric] - a[metric]).slice(0, 15);
  const labels = sorted.map(d => d.symbol);
  const values = sorted.map(d => d[metric]);
  const colors = values.map(v => v >= 0 ? `${C.green}BB` : `${C.red}BB`);

  return new Chart(ctx, {
    type: 'bar',
    data: {
      labels,
      datasets: [{
        label: metric.replace('_', ' '),
        data: values,
        backgroundColor: colors,
        borderRadius: 3,
      }],
    },
    options: {
      responsive: true,
      maintainAspectRatio: false,
      indexAxis: 'y',
      animation: { duration: 300 },
      plugins: {
        tooltip: {
          callbacks: {
            label: ctx => {
              const d = sorted[ctx.dataIndex];
              return [`Profit: ${fmt.currency(d.net_profit)}`, `Trades: ${d.trade_count}`, `Win Rate: ${d.win_rate}%`];
            },
          },
        },
      },
      scales: {
        x: { grid: { color: C.grid }, ticks: { callback: v => `$${v.toLocaleString()}`, font: { family: 'IBM Plex Mono', size: 10 } } },
        y: { grid: { display: false }, ticks: { font: { family: 'IBM Plex Mono', size: 11, weight: '600' } } },
      },
    },
  });
}


// =============================================================================
// 10. DURATION DISTRIBUTION
// =============================================================================

function renderDurationChart(canvasId, data) {
  destroyChart(canvasId);
  const ctx = document.getElementById(canvasId);
  if (!ctx || !data?.length) return;

  return new Chart(ctx, {
    type: 'bar',
    data: {
      labels: data.map(d => d.label),
      datasets: [
        { label: 'Wins',   data: data.map(d => d.wins),   backgroundColor: `${C.green}BB`, borderRadius: 3 },
        { label: 'Losses', data: data.map(d => d.losses), backgroundColor: `${C.red}BB`,   borderRadius: 3 },
      ],
    },
    options: {
      responsive: true,
      maintainAspectRatio: false,
      plugins: {
        legend: { display: true, position: 'top', labels: { boxWidth: 10, font: { size: 11 } } },
      },
      scales: {
        x: { stacked: false, grid: { display: false }, ticks: { font: { size: 10 } } },
        y: { stacked: false, grid: { color: C.grid } },
      },
    },
  });
}


// =============================================================================
// 11. PORTFOLIO ALLOCATION DONUT
// =============================================================================

function renderPortfolioDonut(canvasId, accounts) {
  destroyChart(canvasId);
  const ctx = document.getElementById(canvasId);
  if (!ctx || !accounts?.length) return;

  const palette = [C.accent, C.blue, C.purple, C.yellow, C.green, C.red, '#06B6D4', '#F97316'];

  return new Chart(ctx, {
    type: 'doughnut',
    data: {
      labels: accounts.map(a => a.label),
      datasets: [{
        data:            accounts.map(a => a.equity),
        backgroundColor: palette.slice(0, accounts.length).map(c => `${c}CC`),
        borderColor:     palette.slice(0, accounts.length),
        borderWidth:     1,
        hoverOffset:     6,
      }],
    },
    options: {
      responsive: true,
      maintainAspectRatio: false,
      plugins: {
        legend: {
          display: true, position: 'right',
          labels: { boxWidth: 12, font: { size: 11 }, padding: 12 },
        },
        tooltip: {
          callbacks: {
            label: ctx => {
              const total = ctx.dataset.data.reduce((a, b) => a + b, 0);
              const pct   = ((ctx.parsed / total) * 100).toFixed(1);
              return `${ctx.label}: ${fmt.currency(ctx.parsed)} (${pct}%)`;
            },
          },
        },
      },
      cutout: '65%',
    },
  });
}


// ── Expose globally ───────────────────────────────────────────────────────────
window.Charts = {
  renderEquityCurve, renderDailyPL, renderProfitDistribution,
  renderMonthlyHeatmap, renderHourlyHeatmap, renderCurrencyExposure,
  renderRollingMetrics, renderDrawdownChart, renderSymbolChart,
  renderDurationChart, renderPortfolioDonut, destroyChart, fmt,
};
