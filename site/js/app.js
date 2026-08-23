// Pixelary Labs Agentic Benchmark App (Vanilla JS + ECharts)

document.addEventListener('DOMContentLoaded', () => {
  const isDev = window.location.hostname === 'localhost' ||
    window.location.hostname === '127.0.0.1' ||
    window.location.hostname === '::1' ||
    window.location.hostname === '0.0.0.0' ||
    window.location.port === '8085' ||
    window.location.search.includes('dev=true');

  const jsonUrl = isDev ? `./results/benchmark-data.json?t=${Date.now()}` : './results/benchmark-data.json';
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

  const sumCompletion = tests.reduce((acc, t) => {
    if (t.earned_score !== undefined && t.max_score) {
      return acc + (t.earned_score / t.max_score * 100.0);
    }
    return acc + (t.run_completion || 0);
  }, 0);
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

  const runMemGb = e.memory_gb !== undefined
    ? Number(e.memory_gb)
    : (tests.length > 0 && tests[0].run_memory_gb !== undefined
      ? tests[0].run_memory_gb
      : (e.model_size_gb || e.llm?.model_size_gb || 28.0));

  const modelName = e.name || e.llm?.name || 'Unknown Model';
  const company = e.company || e.llm?.company || '';
  const baseModel = e.base_model || e.parent_model || modelName;
  const kvQuant = e.kv_quant || e.llm?.kv_quant || 'FP16';
  const contextLength = e.context_length || 262144;
  const llmServer = e.llm_server || e.llm?.llm_server || e.server || 'vLLM';
  const speculativeDecoding = e.speculative_decoding || e.llm?.speculative_decoding || 'off';
  const harnessName = typeof e.harness === 'string' ? e.harness : (e.harness?.name || 'N/A');
  const harnessVersion = e.harness_version || '1.18.18';
  const reasoning = e.reasoning || e.harness?.reasoning || e.harness?.reasoning_effort || 'off';
  const benchmarkDate = e.benchmark_date || (e.benchmark_start_time ? e.benchmark_start_time.split('T')[0] : '') || e.release_date || '';
  const launchConfig = e.launch_config || '';
  const taskSpeed = e.task_speed !== undefined ? e.task_speed : (e.summary_metrics?.task_speed ?? 0);
  const intelligenceDensity = e.intelligence_density !== undefined ? e.intelligence_density : (e.summary_metrics?.intelligence_density ?? 0);

  return {
    raw: e,
    tests,
    intelligence,
    timeSec,
    runMemGb,
    memoryGb: Math.round(runMemGb),
    modelName,
    company,
    baseModel,
    kvQuant,
    contextLength,
    benchmarkDate,
    llmServer,
    speculativeDecoding,
    reasoning,
    harnessName,
    harnessVersion,
    launchConfig,
    taskSpeed,
    intelligenceDensity
  };
}

function initDashboard(data) {
  const evaluations = Array.isArray(data.evaluations) ? data.evaluations : [];

  const models = evaluations.map(e => {
    const m = extractEvalMetrics(e);
    return {
      name: m.modelName,
      family: m.company,
      base_model: m.baseModel,
      kv_quant: m.kvQuant,
      context_length: m.contextLength,
      memory_gb: m.memoryGb,
      benchmark_date: m.benchmarkDate,
      intelligence: m.intelligence,
      task_speed: m.taskSpeed,
      intelligence_density: m.intelligenceDensity,
      llm_server: m.llmServer,
      speculative_decoding: m.speculativeDecoding,
      harness_name: m.harnessName,
      harness_version: m.harnessVersion,
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
        <td colspan="7">
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

function formatChartModelLabel(name) {
  if (!name || typeof name !== 'string') return '';

  // Match the right-most parameter size / architecture boundary e.g. "26B-", "A4B-", "70B-"
  const matches = [...name.matchAll(/(?:\d+|[A-Z]\d+)[Bb]-/g)];
  if (matches.length > 0) {
    const lastMatch = matches[matches.length - 1];
    const splitIdx = lastMatch.index + lastMatch[0].length;
    return name.slice(0, splitIdx) + '\n' + name.slice(splitIdx);
  }

  // Fallback: find any last "B-" or "b-"
  const lastB = Math.max(name.lastIndexOf('B-'), name.lastIndexOf('b-'));
  if (lastB !== -1) {
    return name.slice(0, lastB + 2) + '\n' + name.slice(lastB + 2);
  }

  // Fallback: for long names (> 16 chars), split near the middle hyphen or space
  if (name.length > 16) {
    const mid = Math.floor(name.length / 2);
    let bestIdx = -1;
    let minDiff = Infinity;
    for (let i = 0; i < name.length; i++) {
      if (name[i] === '-' || name[i] === '_' || name[i] === ' ') {
        const diff = Math.abs(i - mid);
        if (diff < minDiff) {
          minDiff = diff;
          bestIdx = i;
        }
      }
    }
    if (bestIdx !== -1) {
      return name.slice(0, bestIdx + 1) + '\n' + name.slice(bestIdx + 1);
    }
  }

  return name;
}

function renderModelSpeedChart(evaluations) {
  const chartEl = document.getElementById('chart-model-speed');
  const chart = getOrCreateChart(chartEl);
  if (!chart) return;

  // Sort evaluations by task_speed descending and take top 10
  const normalized = evaluations.map(e => extractEvalMetrics(e));
  const sorted = normalized.sort((a, b) => (b.taskSpeed || 0) - (a.taskSpeed || 0)).slice(0, 10);

  const modelLabels = sorted.map(m => m.modelName);
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
      bottom: '24%',
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
        fontSize: 10,
        lineHeight: 13,
        formatter: function (value) {
          return formatChartModelLabel(value);
        }
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

  chart.off('click');
  chart.on('click', function (params) {
    const rawEval = sorted[params.dataIndex]?.raw;
    if (rawEval) {
      const evalId = rawEval.eval_id || rawEval.trace_id;
      if (evalId) {
        window.location.href = `./trace.html?eval_id=${encodeURIComponent(evalId)}`;
      } else {
        window.location.href = './trace.html';
      }
    }
  });
}

function renderModelDensityChart(evaluations) {
  const chartEl = document.getElementById('chart-model-density');
  const chart = getOrCreateChart(chartEl);
  if (!chart) return;

  // Sort evaluations by intelligence_density descending and take top 10
  const normalized = evaluations.map(e => extractEvalMetrics(e));
  const sorted = normalized.sort((a, b) => (b.intelligenceDensity || 0) - (a.intelligenceDensity || 0)).slice(0, 10);

  const modelLabels = sorted.map(m => m.modelName);
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
      bottom: '24%',
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
        fontSize: 10,
        lineHeight: 13,
        formatter: function (value) {
          return formatChartModelLabel(value);
        }
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
            { offset: 1, color: '#34d399' }
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

  chart.off('click');
  chart.on('click', function (params) {
    const rawEval = sorted[params.dataIndex]?.raw;
    if (rawEval) {
      const evalId = rawEval.eval_id || rawEval.trace_id;
      if (evalId) {
        window.location.href = `./trace.html?eval_id=${encodeURIComponent(evalId)}`;
      } else {
        window.location.href = './trace.html';
      }
    }
  });
}

function renderTopScatterChart(evaluations, viewMode = 'time') {
  const chartEl = document.getElementById('chart-top-scatter');
  const chart = getOrCreateChart(chartEl);
  if (!chart) return;

  // 1. Prepare raw data points
  const rawPoints = [];
  evaluations.forEach((e, idx) => {
    const m = extractEvalMetrics(e);
    const yVal = m.intelligence;
    const xVal = viewMode === 'time' ? m.timeSec : m.runMemGb;
    if (xVal !== undefined && yVal !== undefined) {
      rawPoints.push({
        id: idx,
        x: xVal,
        y: yVal,
        name: m.modelName,
        rawEval: e
      });
    }
  });

  if (rawPoints.length === 0) return;

  // 2. Identify Pareto Frontier Points
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

  // Navigate to trace viewer on dot click
  chart.off('click');
  chart.on('click', function (params) {
    if (params.data && params.data.rawEval) {
      const raw = params.data.rawEval;
      const evalId = raw.eval_id || raw.trace_id;
      if (evalId) {
        window.location.href = `./trace.html?eval_id=${encodeURIComponent(evalId)}`;
      } else {
        window.location.href = './trace.html';
      }
    }
  });
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
      (m.base_model && m.base_model.toLowerCase().includes(filterText)) ||
      (m.llm_server && m.llm_server.toLowerCase().includes(filterText)) ||
      (m.speculative_decoding && m.speculative_decoding.toLowerCase().includes(filterText)) ||
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
          <td colspan="7" style="text-align: center; padding: 2.5rem 1rem; color: var(--text-muted);">
            🔍 No models found
          </td>
        </tr>
      `;
      return;
    }

    const cls = (key) => key === currentSortKey ? 'sort-active' : '';

    tbody.innerHTML = filtered.map(m => {
      const evalId = m.eval_id || (m.raw && (m.raw.eval_id || m.raw.trace_id)) || m.trace_id || '';
      const traceHref = evalId ? `./trace.html?eval_id=${encodeURIComponent(evalId)}` : './trace.html';

      return `
        <tr onclick="window.location.href='${traceHref}'" style="cursor: pointer;" title="Click to view detailed execution trace log">
          <td class="model-name ${cls('name')}">
            <a href="${traceHref}" style="color:inherit; text-decoration:none; display:inline-block;" onclick="event.stopPropagation();">
              <strong>${escapeHtml(m.name)}</strong>
            </a>
            <br/><span style="font-size:0.75rem; color:#6b7280;">${m.base_model || m.family}</span>
            <div class="mobile-sub-info">${escapeHtml(m.harness_name)} • ${escapeHtml(m.reasoning)} • ${escapeHtml(m.kv_quant)}</div>
          </td>
          <td class="${cls('task_speed')}">${Number(m.task_speed).toFixed(1)}</td>
          <td class="${cls('intelligence_density')}">${Number(m.intelligence_density).toFixed(1)}</td>
          <td class="${cls('intelligence')}">${Number(m.intelligence).toFixed(1)}%</td>
          <td class="${cls('memory_gb')}">${Math.round(m.memory_gb)} GB</td>
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
        currentSortOrder = ['name', 'harness_name', 'reasoning'].includes(key) ? 'asc' : 'desc';
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
      <span style="display:inline-block; padding:2px 8px; border-radius:10px; font-family:var(--font-mono, monospace); font-size:0.75rem; font-weight:600; background:rgba(249,115,22,0.1); color:#ea580c; border:1px solid rgba(249,115,22,0.25);">${escapeHtml(m.company || m.parentModel)}</span>
    </div>

    <div style="min-width: 210px;">
      ${row('Intelligence', m.intelligence)}
      ${row('Completion Time', m.timeSec + ' sec')}
      ${row('Memory Use', m.memoryGb + ' GB')}
      ${row('Server', m.llmServer)}
      ${row('Speculative', m.speculativeDecoding)}
      ${row('Harness', m.harnessName)}
      ${row('Reasoning', m.reasoning)}
      ${row('KV Cache Quant', m.kvQuant)}
    </div>
  `;
}
