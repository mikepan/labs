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

function initDashboard(data) {
  const evaluations = data.evaluations || [];

  const models = evaluations.map(e => {
    const tests = e.test_results ? Object.values(e.test_results) : [];
    const sumCompletion = tests.reduce((acc, t) => acc + (t.run_completion || 0), 0);
    const intel = e.summary_metrics.task_intelligence !== undefined ? e.summary_metrics.task_intelligence : Math.round(sumCompletion * 10) / 10;
    const runMemGb = tests.length > 0 ? (tests.reduce((acc, t) => acc + (t.run_memory_gb || 0), 0) / tests.length) : (e.llm.model_size_gb || 16);
    return {
      name: e.llm.name,
      family: e.llm.company,
      param_size: e.llm.param_size,
      quant: e.llm.model_quant,
      memory_gb: Math.round(runMemGb * 10) / 10,
      task_intelligence: intel,
      task_speed: e.summary_metrics.task_speed,
      intelligence_density: e.summary_metrics.intelligence_density,
      harness_name: e.harness.name,
      reasoning_effort: e.harness.reasoning_effort || 'off',
      harness: e.harness,
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

function deriveQuantizationAnalysis(evaluations) {
  const quantGroups = {};
  evaluations.forEach(e => {
    const quant = e.llm.model_quant;
    if (!quantGroups[quant]) {
      quantGroups[quant] = {
        count: 0,
        sumIntelligence: 0,
        sumSyntaxErrors: 0
      };
    }
    const tests = e.test_results ? Object.values(e.test_results) : [];
    const sumCompletion = tests.reduce((acc, t) => acc + (t.run_completion || 0), 0);
    const intel = e.summary_metrics.task_intelligence !== undefined ? e.summary_metrics.task_intelligence : sumCompletion;
    const syntaxErrRate = Math.max(1.2, Math.round((100 - intel) * 0.65 * 10) / 10);

    quantGroups[quant].count += 1;
    quantGroups[quant].sumIntelligence += intel;
    quantGroups[quant].sumSyntaxErrors += syntaxErrRate;
  });

  const targetOrder = ['NVFP4', 'Q8_0', 'Q6_K', 'Q4_K_M', 'Q3_K_M', 'Q2_K', 'IQ2_XXS'];

  const sortedKeys = Object.keys(quantGroups).sort((a, b) => {
    const idxA = targetOrder.indexOf(a);
    const idxB = targetOrder.indexOf(b);
    if (idxA !== -1 && idxB !== -1) return idxA - idxB;
    if (idxA !== -1) return -1;
    if (idxB !== -1) return 1;
    return a.localeCompare(b);
  });

  return sortedKeys.map(quant => {
    const g = quantGroups[quant];
    return {
      quant: quant,
      intelligence: Math.round((g.sumIntelligence / g.count) * 10) / 10,
      tool_call_syntax_error_rate: Math.round((g.sumSyntaxErrors / g.count) * 10) / 10
    };
  });
}

function deriveHarnessEvaluations(evaluations) {
  const harnessGroups = {};
  evaluations.forEach(e => {
    const name = e.harness.name + (e.harness.launch_config ? ` (${e.harness.launch_config.split(',')[0]})` : '');
    if (!harnessGroups[name]) {
      harnessGroups[name] = {
        count: 0,
        sumTaskSpeed: 0,
        sumIntelligence: 0
      };
    }
    harnessGroups[name].count += 1;
    harnessGroups[name].sumTaskSpeed += e.summary_metrics.task_speed;
    harnessGroups[name].sumIntelligence += e.summary_metrics.task_intelligence;
  });

  return Object.keys(harnessGroups).map(name => {
    const g = harnessGroups[name];
    return {
      harness_name: name,
      task_speed: Math.round((g.sumTaskSpeed / g.count) * 10) / 10,
      intelligence: Math.round((g.sumIntelligence / g.count) * 10) / 10
    };
  });
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
  const sorted = [...evaluations].sort((a, b) => (b.summary_metrics.task_speed || 0) - (a.summary_metrics.task_speed || 0)).slice(0, 10);

  const modelLabels = sorted.map(e => `${e.llm.name} (${e.llm.model_quant})`);
  const speedValues = sorted.map(e => Number(e.summary_metrics.task_speed || 0).toFixed(1));

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
  const sorted = [...evaluations].sort((a, b) => (b.summary_metrics.intelligence_density || 0) - (a.summary_metrics.intelligence_density || 0)).slice(0, 10);

  const modelLabels = sorted.map(e => `${e.llm.name} (${e.llm.model_quant})`);
  const densityValues = sorted.map(e => Number(e.summary_metrics.intelligence_density || 0).toFixed(1));

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
    const tests = e.test_results ? Object.values(e.test_results) : [];
    const timeSec = tests.length > 0 ? (tests.reduce((acc, t) => acc + (t.run_time_sec || t.completion_time_sec || 0), 0) / tests.length) : 200;
    const runMemGb = tests.length > 0 ? (tests.reduce((acc, t) => acc + (t.run_memory_gb || 0), 0) / tests.length) : (e.llm.model_size_gb || 16);
    const derivedIntel = tests.reduce((acc, t) => acc + (t.run_completion || 0), 0);
    const intelligence = e.summary_metrics.task_intelligence !== undefined ? e.summary_metrics.task_intelligence : Math.round(derivedIntel * 10) / 10;
    const xVal = viewMode === 'time' ? timeSec : runMemGb;

    return {
      id: idx,
      x: xVal,
      y: intelligence,
      name: e.llm.name,
      quant: e.llm.model_quant,
      memoryGb: Math.round(runMemGb * 10) / 10,
      timeSec: Math.round(timeSec),
      harnessName: e.harness.name,
      kvQuant: e.llm.kv_quant || 'FP16',
      reasoningEffort: e.harness.reasoning_effort || 'off',
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
      value: [p.x, p.y, p.harnessName, p.quant, p.memoryGb, p.timeSec, cls.tier, p.kvQuant, p.reasoningEffort],
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
  if (!tbody) return;

  function updateTable(filterText = '') {
    const filtered = models.filter(m =>
      m.name.toLowerCase().includes(filterText.toLowerCase()) ||
      m.family.toLowerCase().includes(filterText.toLowerCase()) ||
      m.quant.toLowerCase().includes(filterText.toLowerCase()) ||
      m.harness_name.toLowerCase().includes(filterText.toLowerCase())
    );

    tbody.innerHTML = filtered.map(m => {
      return `
        <tr>
          <td class="model-name">
            ${escapeHtml(m.name)}
            <br/><span style="font-size:0.75rem; color:#6b7280;">${m.param_size} • ${m.family}</span>
          </td>
          <td><code>${escapeHtml(m.quant)}</code></td>
          <td>${escapeHtml(m.harness_name)}</td>
          <td><span style="font-family:var(--font-mono); font-size:0.85rem; font-weight:500;">${escapeHtml(m.reasoning_effort)}</span></td>
          <td class="metric-highlight">${Number(m.task_speed).toFixed(1)}</td>
          <td class="metric-highlight">${Number(m.intelligence_density).toFixed(1)}</td>
          <td>${m.task_intelligence}%</td>
          <td>${m.memory_gb} GB</td>
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

function formatModelCardTooltip(evalRecord) {
  if (!evalRecord) return '';

  const llm = evalRecord.llm || {};
  const harness = evalRecord.harness || {};
  const metrics = evalRecord.summary_metrics || {};
  const tests = evalRecord.test_results ? Object.values(evalRecord.test_results) : [];

  const modelName = llm.name || 'Unknown Model';
  const quant = llm.model_quant || 'FP16';

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

  const memoryGb = Math.round(runMemGb * 10) / 10;
  const harnessName = harness.name || 'N/A';
  const reasoningEffort = harness.reasoning_effort || 'off';
  const kvQuant = llm.kv_quant || 'FP16';

  const row = (label, val) => `
    <div style="display:flex; align-items:baseline; justify-content:space-between; font-size:0.825rem; margin-bottom:0.3rem;">
      <span style="color:#64748b; white-space:nowrap;">${label}</span>
      <span style="flex:1; border-bottom:1px dashed rgba(148, 163, 184, 0.35); margin:0 0.4rem 0.2rem;"></span>
      <span style="font-weight:600; color:#0f172a; white-space:nowrap;">${val}</span>
    </div>
  `;

  return `
    <div style="font-weight:600; color:#0f172a; font-size:0.95rem; margin-bottom:4px;">${escapeHtml(modelName)}</div>
    <div style="margin-bottom:10px;">
      <span style="display:inline-block; padding:2px 8px; border-radius:10px; font-family:var(--font-mono, monospace); font-size:0.75rem; font-weight:600; background:rgba(249,115,22,0.1); color:#ea580c; border:1px solid rgba(249,115,22,0.25);">${escapeHtml(quant)}</span>
    </div>

    <div style="min-width: 210px;">
      ${row('Intelligence', intelligence)}
      ${row('Completion Time', timeSec + ' sec')}
      ${row('Memory Use', memoryGb + ' GB')}
      ${row('Harness', harnessName)}
      ${row('Reasoning Effort', reasoningEffort)}
      ${row('KV Cache Quant', kvQuant)}
    </div>
  `;
}
