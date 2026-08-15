// Pixelary Labs Agentic Benchmark App (Vanilla JS + ECharts)

document.addEventListener('DOMContentLoaded', () => {
  const isDev = window.location.hostname === 'localhost' ||
    window.location.hostname === '127.0.0.1' ||
    window.location.search.includes('dev=true');

  const jsonUrl = isDev ? `./data/benchmark-data.json?t=${Date.now()}` : './data/benchmark-data.json';
  const fetchOptions = isDev ? { cache: 'no-store' } : {};

  fetch(jsonUrl, fetchOptions)
    .then(response => {
      if (!response.ok) {
        throw new Error(`HTTP error! status: ${response.status}`);
      }
      return response.json();
    })
    .then(data => {
      initDashboard(data);
    })
    .catch(error => {
      console.error('Failed to load benchmark data:', error);
    });
});

function initDashboard(data) {
  renderQuantChart(data.quantization_analysis);
  renderHarnessChart(data.harness_evaluations);
  renderVelocityChart(data.models);
  renderLeaderboard(data.models);
}

function renderQuantChart(quantData) {
  const chartEl = document.getElementById('chart-quant-degradation');
  if (!chartEl) return;

  const chart = echarts.init(chartEl);
  
  const categories = quantData.map(d => d.quant);
  const singleTurn = quantData.map(d => d.single_turn_accuracy);
  const agenticRate = quantData.map(d => d.agentic_completion_rate);
  const syntaxErrors = quantData.map(d => d.tool_call_syntax_error_rate);

  const option = {
    backgroundColor: 'transparent',
    tooltip: {
      trigger: 'axis',
      backgroundColor: 'rgba(255, 255, 255, 0.96)',
      borderColor: '#e2e8f0',
      shadowColor: 'rgba(0, 0, 0, 0.08)',
      shadowBlur: 12,
      textStyle: { color: '#0f172a', fontFamily: 'Inter' }
    },
    legend: {
      data: ['Single-Turn Accuracy (%)', 'Agentic Task Completion (%)', 'Tool-Call Syntax Error Rate (%)'],
      top: 0,
      textStyle: { color: '#64748b', fontFamily: 'Inter' }
    },
    grid: {
      left: '3%',
      right: '4%',
      bottom: '10%',
      top: '15%',
      containLabel: true
    },
    xAxis: {
      type: 'category',
      data: categories,
      axisLine: { lineStyle: { color: '#cbd5e1' } },
      axisLabel: { color: '#64748b', fontFamily: 'Inter', interval: 0, rotate: 15 }
    },
    yAxis: [
      {
        type: 'value',
        name: 'Success / Accuracy (%)',
        max: 100,
        axisLine: { lineStyle: { color: '#cbd5e1' } },
        splitLine: { lineStyle: { color: '#f1f5f9' } },
        axisLabel: { color: '#64748b', fontFamily: 'Inter' }
      },
      {
        type: 'value',
        name: 'Error Rate (%)',
        max: 70,
        axisLine: { lineStyle: { color: '#cbd5e1' } },
        splitLine: { show: false },
        axisLabel: { color: '#e11d48', fontFamily: 'Inter' }
      }
    ],
    series: [
      {
        name: 'Single-Turn Accuracy (%)',
        type: 'line',
        smooth: true,
        data: singleTurn,
        itemStyle: { color: '#0284c7' },
        lineStyle: { width: 3 }
      },
      {
        name: 'Agentic Task Completion (%)',
        type: 'line',
        smooth: true,
        data: agenticRate,
        itemStyle: { color: '#f97316' },
        lineStyle: { width: 4 },
        areaStyle: {
          color: new echarts.graphic.LinearGradient(0, 0, 0, 1, [
            { offset: 0, color: 'rgba(249, 115, 22, 0.25)' },
            { offset: 1, color: 'rgba(249, 115, 22, 0.0)' }
          ])
        }
      },
      {
        name: 'Tool-Call Syntax Error Rate (%)',
        type: 'bar',
        yAxisIndex: 1,
        data: syntaxErrors,
        itemStyle: { color: 'rgba(225, 29, 72, 0.5)' },
        barWidth: 20
      }
    ]
  };

  chart.setOption(option);
  window.addEventListener('resize', () => chart.resize());
}

function renderHarnessChart(harnessData) {
  const chartEl = document.getElementById('chart-harness-impact');
  if (!chartEl) return;

  const chart = echarts.init(chartEl);

  const names = harnessData.map(d => d.harness_name);
  const crPerHour = harnessData.map(d => d.cr_per_hour);
  const completionRate = harnessData.map(d => d.completion_rate_pct);

  const option = {
    backgroundColor: 'transparent',
    tooltip: {
      trigger: 'axis',
      backgroundColor: 'rgba(255, 255, 255, 0.96)',
      borderColor: '#e2e8f0',
      shadowColor: 'rgba(0, 0, 0, 0.08)',
      shadowBlur: 12,
      textStyle: { color: '#0f172a', fontFamily: 'Inter' }
    },
    legend: {
      data: ['Completion Rate per Hour (CR/hr)', 'Success Rate (%)'],
      top: 0,
      textStyle: { color: '#64748b', fontFamily: 'Inter' }
    },
    grid: {
      left: '3%',
      right: '4%',
      bottom: '10%',
      top: '15%',
      containLabel: true
    },
    xAxis: {
      type: 'category',
      data: names,
      axisLine: { lineStyle: { color: '#cbd5e1' } },
      axisLabel: { color: '#64748b', fontFamily: 'Inter', interval: 0, rotate: 10 }
    },
    yAxis: {
      type: 'value',
      axisLine: { lineStyle: { color: '#cbd5e1' } },
      splitLine: { lineStyle: { color: '#f1f5f9' } },
      axisLabel: { color: '#64748b', fontFamily: 'Inter' }
    },
    series: [
      {
        name: 'Completion Rate per Hour (CR/hr)',
        type: 'bar',
        data: crPerHour,
        itemStyle: {
          color: new echarts.graphic.LinearGradient(0, 0, 0, 1, [
            { offset: 0, color: '#ea580c' },
            { offset: 1, color: '#fb923c' }
          ]),
          borderRadius: [6, 6, 0, 0]
        },
        barWidth: 32
      },
      {
        name: 'Success Rate (%)',
        type: 'line',
        smooth: true,
        data: completionRate,
        itemStyle: { color: '#059669' },
        lineStyle: { width: 3 }
      }
    ]
  };

  chart.setOption(option);
  window.addEventListener('resize', () => chart.resize());
}

function renderVelocityChart(modelsData) {
  const chartEl = document.getElementById('chart-velocity-comparison');
  if (!chartEl) return;

  const chart = echarts.init(chartEl);

  // Filter out low quants for clean chart, or include all
  const seriesData = modelsData.map(m => ({
    name: m.name,
    value: [m.vram_gb, m.cr_per_hour, m.agent_completion_rate, m.tokens_per_sec, m.quant]
  }));

  const option = {
    backgroundColor: 'transparent',
    tooltip: {
      backgroundColor: 'rgba(255, 255, 255, 0.96)',
      borderColor: '#e2e8f0',
      shadowColor: 'rgba(0, 0, 0, 0.08)',
      shadowBlur: 12,
      textStyle: { color: '#0f172a', fontFamily: 'Inter' },
      formatter: function (params) {
        const d = params.data;
        return `
          <div style="font-weight:600; color:#ea580c; margin-bottom:4px;">${d.name} (${d.value[4]})</div>
          <div>Memory Footprint: <strong>${d.value[0]} GB</strong></div>
          <div>Completion Rate / Hr: <strong>${d.value[1]} CR/hr</strong></div>
          <div>Agentic Success: <strong>${d.value[2]}%</strong></div>
          <div>Raw Token Speed: <strong>${d.value[3]} tok/s</strong></div>
        `;
      }
    },
    grid: {
      left: '4%',
      right: '4%',
      bottom: '10%',
      top: '12%',
      containLabel: true
    },
    xAxis: {
      type: 'value',
      name: 'VRAM / RAM Footprint (GB)',
      nameLocation: 'middle',
      nameGap: 30,
      axisLine: { lineStyle: { color: '#cbd5e1' } },
      splitLine: { lineStyle: { color: '#f1f5f9' } },
      axisLabel: { color: '#64748b', fontFamily: 'Inter' }
    },
    yAxis: {
      type: 'value',
      name: 'Completion Rate per Hour (CR/hr)',
      axisLine: { lineStyle: { color: '#cbd5e1' } },
      splitLine: { lineStyle: { color: '#f1f5f9' } },
      axisLabel: { color: '#64748b', fontFamily: 'Inter' }
    },
    series: [
      {
        name: 'Model Velocity',
        type: 'scatter',
        symbolSize: function (data) {
          return Math.max(14, data[2] * 0.45);
        },
        data: seriesData,
        itemStyle: {
          color: function (params) {
            const acc = params.data.value[2];
            if (acc > 75) return '#059669';
            if (acc > 50) return '#f97316';
            return '#e11d48';
          },
          shadowBlur: 8,
          shadowColor: 'rgba(0, 0, 0, 0.1)'
        }
      }
    ]
  };

  chart.setOption(option);
  window.addEventListener('resize', () => chart.resize());
}

function renderLeaderboard(models) {
  const tbody = document.getElementById('leaderboard-body');
  const searchInput = document.getElementById('model-search');
  if (!tbody) return;

  function updateTable(filterText = '') {
    const filtered = models.filter(m =>
      m.name.toLowerCase().includes(filterText.toLowerCase()) ||
      m.family.toLowerCase().includes(filterText.toLowerCase()) ||
      m.quant.toLowerCase().includes(filterText.toLowerCase())
    );

    tbody.innerHTML = filtered.map(m => {
      let badgeClass = 'badge-default';
      if (m.status.includes('Recommended')) badgeClass = 'badge-recommended';
      else if (m.status.includes('Intelligence')) badgeClass = 'badge-intelligence';
      else if (m.status.includes('Reasoning')) badgeClass = 'badge-reasoning';
      else if (m.status.includes('Velocity')) badgeClass = 'badge-velocity';
      else if (m.status.includes('Warning')) badgeClass = 'badge-warning';

      return `
        <tr>
          <td class="model-name">
            ${escapeHtml(m.name)}
            <br/><span style="font-size:0.75rem; color:#6b7280;">${m.param_size} • ${m.family}</span>
          </td>
          <td><span class="badge ${badgeClass}">${escapeHtml(m.status)}</span></td>
          <td><code>${escapeHtml(m.quant)}</code></td>
          <td class="metric-highlight">${m.cr_per_hour} CR/hr</td>
          <td class="metric-highlight">${m.cr_per_gb}</td>
          <td>${m.agent_completion_rate}%</td>
          <td>${m.single_turn_acc}%</td>
          <td>${m.vram_gb} GB</td>
          <td>${m.tokens_per_sec} tok/s</td>
        </tr>
      `;
    }).join('');
  }

  updateTable();

  if (searchInput) {
    searchInput.addEventListener('input', (e) => {
      updateTable(e.target.value);
    });
  }
}

function escapeHtml(str) {
  if (!str) return '';
  return str.replace(/[&<>"']/g, function (m) {
    return {
      '&': '&amp;',
      '<': '&lt;',
      '>': '&gt;',
      '"': '&quot;',
      "'": '&#039;'
    }[m];
  });
}
