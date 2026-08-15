// Pixelary Labs Agentic Benchmark App (Vanilla JS + ECharts)

document.addEventListener('DOMContentLoaded', () => {
  const isDev = window.location.hostname === 'localhost' ||
    window.location.hostname === '127.0.0.1' ||
    window.location.hostname === '::1' ||
    window.location.hostname === '0.0.0.0' ||
    window.location.port === '8085' ||
    window.location.search.includes('dev=true');

  const jsonUrl = isDev ? `./data/benchmark-data.json?t=${Date.now()}` : './data/benchmark-data.json';
  const fetchOptions = isDev ? { cache: 'no-store', headers: { 'Pragma': 'no-cache', 'Cache-Control': 'no-cache' } } : {};

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
      handleFetchError(error);
    });
});

/**
 * Extracts and normalizes benchmark evaluation metrics for a single evaluation record.
 */
function extractEvalMetrics(e) {
  if (!e) return {};

  const llm = e.llm || {};
  const harness = e.harness || {};
  const metrics = e.summary_metrics || {};
  const tests = e.test_results ? Object.values(e.test_results) : [];

  const sumCompletion = tests.reduce((acc, t) => acc + (t.run_completion || 0), 0);
  const intelligence = metrics.task_intelligence !== undefined
    ? metrics.task_intelligence
    : Math.round(sumCompletion * 10) / 10;

  const timeSec = tests.length > 0
    ? Math.round(tests.reduce((acc, t) => acc + (t.run_time_sec || t.completion_time_sec || 0), 0) / tests.length)
    : 0;

  const runMemGb = tests.length > 0
    ? (tests.reduce((acc, t) => acc + (t.run_memory_gb || 0), 0) / tests.length)
    : (llm.model_size_gb || 0);

  return {
    llm,
    harness,
    metrics,
    tests,
    intelligence,
    timeSec,
    runMemGb,
    memoryGb: Math.round(runMemGb),
    modelName: llm.name || 'Unknown Model',
    quant: llm.model_quant || 'FP16',
    kvQuant: llm.kv_quant || 'FP16',
    reasoningEffort: harness.reasoning_effort || 'off',
    harnessName: harness.name || 'N/A',
    taskSpeed: metrics.task_speed ?? 0,
    intelligenceDensity: metrics.intelligence_density ?? 0
  };
}

function initDashboard(data) {
  const evaluations = data.evaluations || [];

  const models = evaluations.map(e => {
    const m = extractEvalMetrics(e);
    return {
      name: m.modelName,
      family: m.llm.company || '',
      param_size: m.llm.param_size || '',
      quant: m.quant,
      memory_gb: m.memoryGb,
      task_intelligence: m.intelligence,
      task_speed: m.taskSpeed,
      intelligence_density: m.intelligenceDensity,
      harness_name: m.harnessName,
      reasoning_effort: m.reasoningEffort,
      harness: m.harness,
      test_results: e.test_results
    };
  });

  let currentScatterView = 'time';

  renderTopScatterChart(evaluations, currentScatterView);
  renderModelSpeedChart(evaluations);
  renderModelDensityChart(evaluations);
  renderLeaderboard(models);

  // Bind toggle buttons for Top Scatter Chart (Time View vs Size View)
  const toggleGroup = document.getElementById('scatter-toggle-group');
  if (toggleGroup) {
    toggleGroup.addEventListener('click', (e) => {
      const btn = e.target.closest('.toggle-btn');
      if (!btn) return;

      const view = btn.getAttribute('data-view');
      if (!view || view === currentScatterView) return;

      currentScatterView = view;
      toggleGroup.querySelectorAll('.toggle-btn').forEach(b => b.classList.remove('active'));
      btn.classList.add('active');

      renderTopScatterChart(evaluations, currentScatterView);
    });
  }
}

function handleFetchError(error) {
  console.error('Failed to load benchmark data:', error);

  const errorHtml = `
    <div class="data-error-state">
      <h4 class="data-error-title">Unable to Load Benchmark Dataset</h4>
    </div>
  `;

  ['chart-top-scatter', 'chart-model-speed', 'chart-model-density'].forEach(id => {
    const el = document.getElementById(id);
    if (el) el.innerHTML = errorHtml;
  });

  const tbody = document.getElementById('leaderboard-body');
  if (tbody) {
    tbody.innerHTML = `
      <tr class="table-error-row">
        <td colspan="8">
          <div class="data-error-state" style="min-height: auto; padding: 2rem 1rem;">
            <h4 class="data-error-title">Leaderboard Data Unavailable</h4>
          </div>
        </td>
      </tr>
    `;
  }
}

function renderModelSpeedChart(evaluations) {
  const chartEl = document.getElementById('chart-model-speed');
  if (!chartEl) return;

  const chart = echarts.init(chartEl);

  // Sort evaluations by task_speed descending and take top 10
  const sorted = [...evaluations].sort((a, b) => (b.summary_metrics?.task_speed || 0) - (a.summary_metrics?.task_speed || 0)).slice(0, 10);

  const modelLabels = sorted.map(e => `${e.llm?.name || 'Unknown'} (${e.llm?.model_quant || 'FP16'})`);
  const speedValues = sorted.map(e => Number(e.summary_metrics?.task_speed || 0).toFixed(1));

  const option = {
    backgroundColor: 'transparent',
    tooltip: {
      trigger: 'axis',
      confine: true,
      backgroundColor: 'rgba(255, 255, 255, 0.96)',
      borderColor: '#e2e8f0',
      shadowColor: 'rgba(0, 0, 0, 0.08)',
      shadowBlur: 12,
      textStyle: { color: '#0f172a', fontFamily: 'Inter' },
      formatter: function (params) {
        const item = params[0];
        const rawEval = sorted[item.dataIndex];
        return formatModelCardTooltip(rawEval);
      }
    },
    grid: {
      left: '3%',
      right: '4%',
      bottom: '22%',
      top: '12%',
      containLabel: true
    },
    xAxis: {
      type: 'category',
      data: modelLabels,
      axisLine: { lineStyle: { color: '#cbd5e1' } },
      axisLabel: {
        color: '#64748b',
        fontFamily: 'Inter',
        interval: 0,
        rotate: 25,
        fontSize: 11
      }
    },
    yAxis: {
      type: 'value',
      name: 'Task Speed',
      axisLine: { lineStyle: { color: '#cbd5e1' } },
      splitLine: { lineStyle: { color: '#f1f5f9' } },
      axisLabel: { color: '#64748b', fontFamily: 'Inter' }
    },
    series: [
      {
        name: 'Task Speed',
        type: 'bar',
        data: speedValues,
        itemStyle: {
          color: new echarts.graphic.LinearGradient(0, 0, 0, 1, [
            { offset: 0, color: '#ea580c' },
            { offset: 1, color: '#fb923c' }
          ]),
          borderRadius: [6, 6, 0, 0]
        },
        barWidth: 28,
        emphasis: {
          itemStyle: {
            color: '#c2410c'
          }
        }
      }
    ]
  };

  chart.setOption(option);
  window.addEventListener('resize', () => chart.resize());
}

function renderModelDensityChart(evaluations) {
  const chartEl = document.getElementById('chart-model-density');
  if (!chartEl) return;

  const chart = echarts.init(chartEl);

  // Sort evaluations by intelligence_density descending and take top 10
  const sorted = [...evaluations].sort((a, b) => (b.summary_metrics?.intelligence_density || 0) - (a.summary_metrics?.intelligence_density || 0)).slice(0, 10);

  const modelLabels = sorted.map(e => `${e.llm?.name || 'Unknown'} (${e.llm?.model_quant || 'FP16'})`);
  const densityValues = sorted.map(e => Number(e.summary_metrics?.intelligence_density || 0).toFixed(1));

  const option = {
    backgroundColor: 'transparent',
    tooltip: {
      trigger: 'axis',
      confine: true,
      backgroundColor: 'rgba(255, 255, 255, 0.96)',
      borderColor: '#e2e8f0',
      shadowColor: 'rgba(0, 0, 0, 0.08)',
      shadowBlur: 12,
      textStyle: { color: '#0f172a', fontFamily: 'Inter' },
      formatter: function (params) {
        const item = params[0];
        const rawEval = sorted[item.dataIndex];
        return formatModelCardTooltip(rawEval);
      }
    },
    grid: {
      left: '3%',
      right: '4%',
      bottom: '22%',
      top: '12%',
      containLabel: true
    },
    xAxis: {
      type: 'category',
      data: modelLabels,
      axisLine: { lineStyle: { color: '#cbd5e1' } },
      axisLabel: {
        color: '#64748b',
        fontFamily: 'Inter',
        interval: 0,
        rotate: 25,
        fontSize: 11
      }
    },
    yAxis: {
      type: 'value',
      name: 'Intelligence Density',
      axisLine: { lineStyle: { color: '#cbd5e1' } },
      splitLine: { lineStyle: { color: '#f1f5f9' } },
      axisLabel: { color: '#64748b', fontFamily: 'Inter' }
    },
    series: [
      {
        name: 'Intelligence Density',
        type: 'bar',
        data: densityValues,
        itemStyle: {
          color: new echarts.graphic.LinearGradient(0, 0, 0, 1, [
            { offset: 0, color: '#059669' },
            { offset: 1, color: '#10b981' }
          ]),
          borderRadius: [6, 6, 0, 0]
        },
        barWidth: 28,
        emphasis: {
          itemStyle: {
            color: '#047857'
          }
        }
      }
    ]
  };

  chart.setOption(option);
  window.addEventListener('resize', () => chart.resize());
}

function renderTopScatterChart(evaluations, viewMode = 'time') {
  const chartEl = document.getElementById('chart-top-scatter');
  if (!chartEl) return;

  let chart = echarts.getInstanceByDom(chartEl);
  if (!chart) {
    chart = echarts.init(chartEl);
    window.addEventListener('resize', () => chart.resize());
  }

  // 1. Extract raw points for Pareto frontier calculation
  const rawPoints = evaluations.map((e, idx) => {
    const m = extractEvalMetrics(e);
    const xVal = viewMode === 'time' ? (m.timeSec || 200) : (m.runMemGb || 16);

    return {
      id: idx,
      x: xVal,
      y: m.intelligence,
      name: m.modelName,
      rawEval: e
    };
  });

  // 2. Identify Pareto Optimal points (Lower X is better, Higher Y is better)
  const paretoPoints = [];
  const dominatedPoints = [];

  rawPoints.forEach(p => {
    let isDominated = false;
    for (let other of rawPoints) {
      if (other.id === p.id) continue;
      if (other.x <= p.x && other.y >= p.y && (other.x < p.x || other.y > p.y)) {
        isDominated = true;
        break;
      }
    }
    if (!isDominated) {
      paretoPoints.push(p);
    } else {
      dominatedPoints.push(p);
    }
  });

  // Sort paretoPoints by X ascending to form the frontier curve
  paretoPoints.sort((a, b) => a.x - b.x);

  // 3. Classify points & assign tier label
  const pointClassifications = new Map();
  paretoPoints.forEach(p => {
    pointClassifications.set(p.id, {
      tier: 'Best-In-Class',
      color: '#059669' // Green
    });
  });

  dominatedPoints.forEach(p => {
    // Interpolate Pareto frontier Y value at p.x
    let frontierY = 0;
    if (p.x <= paretoPoints[0].x) {
      frontierY = paretoPoints[0].y;
    } else if (p.x >= paretoPoints[paretoPoints.length - 1].x) {
      frontierY = paretoPoints[paretoPoints.length - 1].y;
    } else {
      for (let i = 0; i < paretoPoints.length - 1; i++) {
        if (paretoPoints[i].x <= p.x && paretoPoints[i + 1].x >= p.x) {
          const t = (p.x - paretoPoints[i].x) / (paretoPoints[i + 1].x - paretoPoints[i].x);
          frontierY = paretoPoints[i].y + t * (paretoPoints[i + 1].y - paretoPoints[i].y);
          break;
        }
      }
    }

    const deficit = frontierY - p.y;
    if (deficit <= 12.0) {
      pointClassifications.set(p.id, {
        tier: 'Average',
        color: '#d97706' // Yellow / Amber
      });
    } else {
      pointClassifications.set(p.id, {
        tier: 'Below Average',
        color: '#e11d48' // Red
      });
    }
  });

  // Group scatter series data by legend category
  const bestInClassData = [];
  const averageData = [];
  const belowAverageData = [];

  rawPoints.forEach(p => {
    const cls = pointClassifications.get(p.id);
    const item = {
      name: p.name,
      value: [p.x, p.y],
      rawEval: p.rawEval
    };
    if (cls.tier === 'Best-In-Class') {
      bestInClassData.push(item);
    } else if (cls.tier === 'Average') {
      averageData.push(item);
    } else {
      belowAverageData.push(item);
    }
  });

  const xAxisName = viewMode === 'time' ? 'Task Completion Time (seconds)' : 'Memory / VRAM Size (GB)';

  const option = {
    animation: false,
    backgroundColor: 'transparent',
    tooltip: {
      confine: true,
      backgroundColor: 'rgba(255, 255, 255, 0.96)',
      borderColor: '#e2e8f0',
      shadowColor: 'rgba(0, 0, 0, 0.08)',
      shadowBlur: 12,
      textStyle: { color: '#0f172a', fontFamily: 'Inter' },
      formatter: function (params) {
        return formatModelCardTooltip(params.data.rawEval);
      }
    },
    legend: {
      data: ['Best-In-Class', 'Average', 'Below Average'],
      top: 0,
      right: '5%',
      textStyle: { color: '#64748b', fontFamily: 'Inter' }
    },
    grid: {
      left: '4%',
      right: '5%',
      bottom: '12%',
      top: '12%',
      containLabel: true
    },
    xAxis: {
      type: 'value',
      name: xAxisName,
      nameLocation: 'middle',
      nameGap: 32,
      axisLine: { lineStyle: { color: '#cbd5e1' } },
      splitLine: { lineStyle: { color: '#f1f5f9' } },
      axisLabel: { color: '#64748b', fontFamily: 'Inter' }
    },
    yAxis: {
      type: 'value',
      name: 'Intelligence',
      max: 100,
      min: 0,
      axisLine: { lineStyle: { color: '#cbd5e1' } },
      splitLine: { lineStyle: { color: '#f1f5f9' } },
      axisLabel: { color: '#64748b', fontFamily: 'Inter' }
    },
    series: [
      {
        name: 'Best-In-Class',
        type: 'scatter',
        symbolSize: 22,
        data: bestInClassData,
        itemStyle: {
          color: '#059669',
          borderWidth: 1.5,
          borderColor: 'rgba(255, 255, 255, 0.9)',
          shadowColor: 'rgba(5, 150, 105, 0.3)',
          shadowBlur: 10
        },
        emphasis: {
          focus: 'self',
          scale: 1.5,
          itemStyle: {
            borderColor: '#ffffff',
            borderWidth: 3.5,
            shadowBlur: 35,
            shadowColor: '#059669',
            opacity: 1
          }
        },
        blur: {
          itemStyle: { opacity: 0.2, shadowBlur: 0 }
        }
      },
      {
        name: 'Average',
        type: 'scatter',
        symbolSize: 22,
        data: averageData,
        itemStyle: {
          color: '#d97706',
          borderWidth: 1.5,
          borderColor: 'rgba(255, 255, 255, 0.9)',
          shadowColor: 'rgba(217, 119, 6, 0.3)',
          shadowBlur: 10
        },
        emphasis: {
          focus: 'self',
          scale: 1.5,
          itemStyle: {
            borderColor: '#ffffff',
            borderWidth: 3.5,
            shadowBlur: 35,
            shadowColor: '#d97706',
            opacity: 1
          }
        },
        blur: {
          itemStyle: { opacity: 0.2, shadowBlur: 0 }
        }
      },
      {
        name: 'Below Average',
        type: 'scatter',
        symbolSize: 22,
        data: belowAverageData,
        itemStyle: {
          color: '#e11d48',
          borderWidth: 1.5,
          borderColor: 'rgba(255, 255, 255, 0.9)',
          shadowColor: 'rgba(225, 29, 72, 0.3)',
          shadowBlur: 10
        },
        emphasis: {
          focus: 'self',
          scale: 1.5,
          itemStyle: {
            borderColor: '#ffffff',
            borderWidth: 3.5,
            shadowBlur: 35,
            shadowColor: '#e11d48',
            opacity: 1
          }
        },
        blur: {
          itemStyle: { opacity: 0.2, shadowBlur: 0 }
        }
      }
    ]
  };

  chart.setOption(option, true);
}

function renderLeaderboard(models) {
  const tbody = document.getElementById('leaderboard-body');
  const searchInput = document.getElementById('model-search');
  const headers = document.querySelectorAll('th.sortable-header');
  if (!tbody) return;

  let currentSortKey = 'task_intelligence';
  let currentSortOrder = 'desc';

  function updateTable() {
    const filterText = searchInput ? searchInput.value.trim().toLowerCase() : '';

    const filtered = models.filter(m =>
      m.name.toLowerCase().includes(filterText) ||
      m.family.toLowerCase().includes(filterText) ||
      m.quant.toLowerCase().includes(filterText) ||
      m.harness_name.toLowerCase().includes(filterText)
    );

    filtered.sort((a, b) => {
      let valA = a[currentSortKey];
      let valB = b[currentSortKey];
      if (typeof valA === 'string') {
        valA = valA.toLowerCase();
        valB = (valB || '').toLowerCase();
        return currentSortOrder === 'asc' ? valA.localeCompare(valB) : valB.localeCompare(valA);
      }
      valA = Number(valA || 0);
      valB = Number(valB || 0);
      return currentSortOrder === 'asc' ? valA - valB : valB - valA;
    });

    headers.forEach(th => {
      const key = th.getAttribute('data-sort');
      const icon = th.querySelector('.sort-icon');
      if (key === currentSortKey) {
        th.classList.add('sort-active');
        if (icon) icon.textContent = currentSortOrder === 'asc' ? ' ▲' : ' ▼';
      } else {
        th.classList.remove('sort-active');
        if (icon) icon.textContent = '';
      }
    });

    if (filtered.length === 0) {
      tbody.innerHTML = `
        <tr>
          <td colspan="8" style="text-align: center; padding: 2.5rem 1rem; color: var(--text-muted);">
            🔍 No models found
          </td>
        </tr>
      `;
      return;
    }

    const cls = (key) => key === currentSortKey ? 'sort-active' : '';

    tbody.innerHTML = filtered.map(m => {
      return `
        <tr>
          <td class="model-name ${cls('name')}">
            <strong>${escapeHtml(m.name)}</strong>
            <br/><span style="font-size:0.75rem; color:#6b7280;">${m.param_size} • ${m.family}</span>
            <div class="mobile-sub-info">${escapeHtml(m.quant)} • ${escapeHtml(m.harness_name)} • ${escapeHtml(m.reasoning_effort)}</div>
          </td>
          <td class="${cls('task_speed')}">${Number(m.task_speed).toFixed(1)}</td>
          <td class="${cls('intelligence_density')}">${Number(m.intelligence_density).toFixed(1)}</td>
          <td class="${cls('task_intelligence')}">${m.task_intelligence}</td>
          <td class="${cls('memory_gb')}">${Math.round(m.memory_gb)} GB</td>
          <td class="hide-mobile ${cls('quant')}"><code>${escapeHtml(m.quant)}</code></td>
          <td class="hide-mobile ${cls('harness_name')}">${escapeHtml(m.harness_name)}</td>
          <td class="hide-mobile ${cls('reasoning_effort')}"><span style="font-family:var(--font-mono); font-size:0.85rem; font-weight:500;">${escapeHtml(m.reasoning_effort)}</span></td>
        </tr>
      `;
    }).join('');
  }

  headers.forEach(th => {
    th.addEventListener('click', () => {
      const key = th.getAttribute('data-sort');
      if (!key) return;
      if (currentSortKey === key) {
        currentSortOrder = currentSortOrder === 'asc' ? 'desc' : 'asc';
      } else {
        currentSortKey = key;
        currentSortOrder = ['name', 'quant', 'harness_name', 'reasoning_effort'].includes(key) ? 'asc' : 'desc';
      }
      updateTable();
    });
  });

  if (searchInput) {
    searchInput.addEventListener('input', () => updateTable());
  }

  updateTable();
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

function formatModelCardTooltip(evalRecord) {
  if (!evalRecord) return '';

  const m = extractEvalMetrics(evalRecord);

  const row = (label, val) => `
    <div style="display:flex; align-items:baseline; justify-content:space-between; font-size:0.825rem; margin-bottom:0.3rem;">
      <span style="color:#64748b; white-space:nowrap;">${label}</span>
      <span style="flex:1; border-bottom:1px dashed rgba(148, 163, 184, 0.35); margin:0 0.4rem 0.2rem;"></span>
      <span style="font-weight:600; color:#0f172a; white-space:nowrap;">${val}</span>
    </div>
  `;

  return `
    <div style="font-weight:600; color:#0f172a; font-size:0.95rem; margin-bottom:4px;">${escapeHtml(m.modelName)}</div>
    <div style="margin-bottom:10px;">
      <span style="display:inline-block; padding:2px 8px; border-radius:10px; font-family:var(--font-mono, monospace); font-size:0.75rem; font-weight:600; background:rgba(249,115,22,0.1); color:#ea580c; border:1px solid rgba(249,115,22,0.25);">${escapeHtml(m.quant)}</span>
    </div>

    <div style="min-width: 210px;">
      ${row('Intelligence', m.intelligence)}
      ${row('Completion Time', m.timeSec + ' sec')}
      ${row('Memory Use', m.memoryGb + ' GB')}
      ${row('Harness', m.harnessName)}
      ${row('Reasoning Effort', m.reasoningEffort)}
      ${row('KV Cache Quant', m.kvQuant)}
    </div>
  `;
}
