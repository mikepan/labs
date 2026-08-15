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
 * Supports both Columnar Matrix objects and legacy nested structures.
 */
function extractEvalMetrics(e) {
  if (!e) return {};

  const testsObj = e.test_results || {};
  const tests = Array.isArray(testsObj) ? testsObj : Object.values(testsObj);

  const sumCompletion = tests.reduce((acc, t) => acc + (t.run_completion || 0), 0);
  const intelligence = e.intelligence !== undefined
    ? e.intelligence
    : (e.summary_metrics?.intelligence !== undefined
      ? e.summary_metrics.intelligence
      : (e.summary_metrics?.task_intelligence !== undefined
        ? e.summary_metrics.task_intelligence
        : Math.round(sumCompletion * 10) / 10));

  const timeSec = tests.length > 0
    ? Math.round(tests.reduce((acc, t) => acc + (t.run_time_sec || t.completion_time_sec || 0), 0) / tests.length)
    : 0;

  const runMemGb = tests.length > 0
    ? (tests.reduce((acc, t) => acc + (t.run_memory_gb || 0), 0) / tests.length)
    : (e.model_size_gb || e.llm?.model_size_gb || 0);

  const paramSizeB = e.param_size_b !== undefined
    ? e.param_size_b
    : (e.llm?.param_size_b !== undefined ? e.llm.param_size_b : (parseInt(e.param_size || e.llm?.param_size, 10) || 0));

  const modelName = e.name || e.llm?.name || 'Unknown Model';
  const company = e.company || e.llm?.company || '';
  const quant = e.model_quant || e.llm?.model_quant || e.quant || 'FP16';
  const kvQuant = e.kv_quant || e.llm?.kv_quant || 'FP16';
  const harnessName = typeof e.harness === 'string' ? e.harness : (e.harness?.name || 'N/A');
  const reasoning = e.reasoning || e.harness?.reasoning || e.harness?.reasoning_effort || 'off';
  const taskSpeed = e.task_speed !== undefined ? e.task_speed : (e.summary_metrics?.task_speed ?? 0);
  const intelligenceDensity = e.intelligence_density !== undefined ? e.intelligence_density : (e.summary_metrics?.intelligence_density ?? 0);

  return {
    raw: e,
    tests,
    intelligence,
    timeSec,
    runMemGb,
    paramSizeB,
    memoryGb: Math.round(runMemGb),
    modelName,
    company,
    quant,
    kvQuant,
    reasoning,
    harnessName,
    taskSpeed,
    intelligenceDensity
  };
}

function initDashboard(data) {
  let evaluations = [];

  if (data.rows && data.columns) {
    const cols = data.columns;
    evaluations = data.rows.map(row => {
      const obj = {};
      cols.forEach((col, idx) => {
        obj[col] = row[idx];
      });
      return obj;
    });
  } else if (Array.isArray(data.evaluations)) {
    evaluations = data.evaluations;
  }

  const models = evaluations.map(e => {
    const m = extractEvalMetrics(e);
    return {
      name: m.modelName,
      family: m.company,
      param_size_b: m.paramSizeB,
      quant: m.quant,
      memory_gb: m.memoryGb,
      intelligence: m.intelligence,
      task_speed: m.taskSpeed,
      intelligence_density: m.intelligenceDensity,
      harness_name: m.harnessName,
      reasoning: m.reasoning,
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

function getOrCreateChart(chartEl) {
  if (!chartEl) return null;
  let chart = echarts.getInstanceByDom(chartEl);
  if (!chart) {
    chart = echarts.init(chartEl);
    if (typeof ResizeObserver !== 'undefined') {
      const ro = new ResizeObserver(() => chart.resize({ animation: { duration: 0 } }));
      ro.observe(chartEl);
      chartEl._resizeObserver = ro;
    } else {
      window.addEventListener('resize', () => chart.resize({ animation: { duration: 0 } }));
    }
  }
  return chart;
}

function renderModelSpeedChart(evaluations) {
  const chartEl = document.getElementById('chart-model-speed');
  const chart = getOrCreateChart(chartEl);
  if (!chart) return;

  // Sort evaluations by task_speed descending and take top 10
  const normalized = evaluations.map(e => extractEvalMetrics(e));
  const sorted = normalized.sort((a, b) => (b.taskSpeed || 0) - (a.taskSpeed || 0)).slice(0, 10);

  const modelLabels = sorted.map(m => `${m.modelName} (${m.quant})`);
  const speedValues = sorted.map(m => Number(m.taskSpeed || 0).toFixed(1));

  const option = {
    backgroundColor: 'transparent',
    tooltip: {
      trigger: 'axis',
      confine: true,
      backgroundColor: 'rgba(255, 255, 255, 0.96)',
      borderColor: '#e2e8f0',
      shadowColor: 'rgba(0, 0, 0, 0.08)',
      shadowBlur: 12,
      textStyle: { color: '#0f172a', fontFamily: 'system-ui, -apple-system, sans-serif' },
      formatter: function (params) {
        const item = params[0];
        const rawEval = sorted[item.dataIndex]?.raw;
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
        fontFamily: 'system-ui, -apple-system, sans-serif',
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
      axisLabel: { color: '#64748b', fontFamily: 'system-ui, -apple-system, sans-serif' }
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
        barWidth: 18,
        emphasis: {
          itemStyle: {
            color: '#c2410c'
          }
        }
      }
    ]
  };

  chart.setOption(option);
}

function renderModelDensityChart(evaluations) {
  const chartEl = document.getElementById('chart-model-density');
  const chart = getOrCreateChart(chartEl);
  if (!chart) return;

  // Sort evaluations by intelligence_density descending and take top 10
  const normalized = evaluations.map(e => extractEvalMetrics(e));
  const sorted = normalized.sort((a, b) => (b.intelligenceDensity || 0) - (a.intelligenceDensity || 0)).slice(0, 10);

  const modelLabels = sorted.map(m => `${m.modelName} (${m.quant})`);
  const densityValues = sorted.map(m => Number(m.intelligenceDensity || 0).toFixed(1));

  const option = {
    backgroundColor: 'transparent',
    tooltip: {
      trigger: 'axis',
      confine: true,
      backgroundColor: 'rgba(255, 255, 255, 0.96)',
      borderColor: '#e2e8f0',
      shadowColor: 'rgba(0, 0, 0, 0.08)',
      shadowBlur: 12,
      textStyle: { color: '#0f172a', fontFamily: 'system-ui, -apple-system, sans-serif' },
      formatter: function (params) {
        const item = params[0];
        const rawEval = sorted[item.dataIndex]?.raw;
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
        fontFamily: 'system-ui, -apple-system, sans-serif',
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
      axisLabel: { color: '#64748b', fontFamily: 'system-ui, -apple-system, sans-serif' }
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
        barWidth: 18,
        emphasis: {
          itemStyle: {
            color: '#047857'
          }
        }
      }
    ]
  };

  chart.setOption(option);
}

function renderTopScatterChart(evaluations, viewMode = 'time') {
  const chartEl = document.getElementById('chart-top-scatter');
  const chart = getOrCreateChart(chartEl);
  if (!chart) return;

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
      textStyle: { color: '#0f172a', fontFamily: 'system-ui, -apple-system, sans-serif' },
      formatter: function (params) {
        return formatModelCardTooltip(params.data.rawEval);
      }
    },
    legend: {
      data: ['Best-In-Class', 'Average', 'Below Average'],
      top: 0,
      right: '5%',
      textStyle: { color: '#64748b', fontFamily: 'system-ui, -apple-system, sans-serif' }
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
      axisLabel: { color: '#64748b', fontFamily: 'system-ui, -apple-system, sans-serif' }
    },
    yAxis: {
      type: 'value',
      name: 'Intelligence',
      max: 100,
      min: 0,
      axisLine: { lineStyle: { color: '#cbd5e1' } },
      splitLine: { lineStyle: { color: '#f1f5f9' } },
      axisLabel: { color: '#64748b', fontFamily: 'system-ui, -apple-system, sans-serif' }
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

  let currentSortKey = 'intelligence';
  let currentSortOrder = 'desc';

  function updateTable() {
    const filterText = searchInput ? searchInput.value.trim().toLowerCase() : '';

    const filtered = models.filter(m =>
      m.name.toLowerCase().includes(filterText) ||
      m.family.toLowerCase().includes(filterText) ||
      m.quant.toLowerCase().includes(filterText) ||
      m.harness_name.toLowerCase().includes(filterText) ||
      m.reasoning.toLowerCase().includes(filterText)
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
            <br/><span style="font-size:0.75rem; color:#6b7280;">${m.param_size_b ? m.param_size_b + 'B' : ''} • ${m.family}</span>
            <div class="mobile-sub-info">${escapeHtml(m.quant)} • ${escapeHtml(m.harness_name)} • ${escapeHtml(m.reasoning)}</div>
          </td>
          <td class="${cls('task_speed')}">${Number(m.task_speed).toFixed(1)}</td>
          <td class="${cls('intelligence_density')}">${Number(m.intelligence_density).toFixed(1)}</td>
          <td class="${cls('intelligence')}">${m.intelligence}</td>
          <td class="${cls('memory_gb')}">${Math.round(m.memory_gb)} GB</td>
          <td class="hide-mobile ${cls('quant')}"><code>${escapeHtml(m.quant)}</code></td>
          <td class="hide-mobile ${cls('harness_name')}">${escapeHtml(m.harness_name)}</td>
          <td class="hide-mobile ${cls('reasoning')}"><span style="font-family:var(--font-mono); font-size:0.85rem; font-weight:500;">${escapeHtml(m.reasoning)}</span></td>
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
        currentSortOrder = ['name', 'quant', 'harness_name', 'reasoning'].includes(key) ? 'asc' : 'desc';
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
      ${row('Reasoning', m.reasoning)}
      ${row('KV Cache Quant', m.kvQuant)}
    </div>
  `;
}
