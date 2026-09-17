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
 */
function extractEvalMetrics(e) {
  if (!e) return {};

  const testsObj = e.test_results || {};
  const tests = Object.values(testsObj);

  const totalTimeSec = tests.reduce((acc, t) => acc + (t.run_time_sec || 0), 0);
  const timeMin = tests.length > 0
    ? Math.round(totalTimeSec / 60)
    : 0;

  const runMemGb = Number(e.memory_gb || 0);

  return {
    raw: e,
    evalId: e.eval_id || '',
    tests,
    intelligence: e.intelligence ?? 0,
    timeMin,
    totalTimeSec,
    runMemGb,
    memoryGb: Math.round(runMemGb),
    modelName: e.name || '',
    company: e.company || '',
    baseModel: e.base_model || e.name || '',
    kvQuant: e.kv_quant || '',
    contextLength: e.context_length || 0,
    benchmarkDate: e.benchmark_date || '',
    llmServer: e.llm_server || '',
    speculativeDecoding: e.speculative_decoding || 'off',
    reasoning: e.reasoning || 'off',
    harnessName: e.harness || '',
    harnessVersion: e.harness_version || '',
    launchConfig: e.launch_config || '',
    taskSpeed: e.task_speed ?? 0,
    intelligenceDensity: e.intelligence_density ?? 0
  };
}

function initDashboard(data) {
  const evaluations = Array.isArray(data.evaluations) ? data.evaluations : [];

  const models = evaluations.map(e => {
    const m = extractEvalMetrics(e);
    return {
      eval_id: m.evalId,
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
  let currentScatterColor = 'base_model';

  renderTopScatterChart(evaluations, currentScatterView, currentScatterColor);
  renderModelSpeedChart(evaluations);
  renderModelDensityChart(evaluations);
  renderHarnessPieCharts(evaluations);
  renderLeaderboard(models);

  // Bind toggle buttons for Top Scatter Chart (Time View vs Size View)
  const viewToggleGroup = document.getElementById('scatter-view-toggle') || document.getElementById('scatter-toggle-group');
  if (viewToggleGroup) {
    viewToggleGroup.addEventListener('click', (e) => {
      const btn = e.target.closest('.toggle-btn');
      if (!btn) return;

      const view = btn.getAttribute('data-view');
      if (!view || view === currentScatterView) return;

      currentScatterView = view;
      viewToggleGroup.querySelectorAll('.toggle-btn').forEach(b => b.classList.remove('active'));
      btn.classList.add('active');

      renderTopScatterChart(evaluations, currentScatterView, currentScatterColor);
    });
  }

  // Bind toggle buttons for Color By (Base Model vs Harness)
  const colorToggleGroup = document.getElementById('scatter-color-toggle');
  if (colorToggleGroup) {
    colorToggleGroup.addEventListener('click', (e) => {
      const btn = e.target.closest('.toggle-btn');
      if (!btn) return;

      const colorMode = btn.getAttribute('data-color');
      if (!colorMode || colorMode === currentScatterColor) return;

      currentScatterColor = colorMode;
      colorToggleGroup.querySelectorAll('.toggle-btn').forEach(b => b.classList.remove('active'));
      btn.classList.add('active');

      renderTopScatterChart(evaluations, currentScatterView, currentScatterColor);
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

  ['chart-top-scatter', 'chart-model-speed', 'chart-model-density', 'chart-harness-tasks', 'chart-harness-time'].forEach(id => {
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

function renderRankingBarChart(containerId, evaluations, { metricKey, yAxisName, gradientColors, hoverColor }) {
  const chartEl = document.getElementById(containerId);
  const chart = getOrCreateChart(chartEl);
  if (!chart) return;

  const normalized = evaluations.map(e => extractEvalMetrics(e));
  const sorted = normalized.sort((a, b) => (b[metricKey] || 0) - (a[metricKey] || 0)).slice(0, 10);
  const modelLabels = sorted.map(m => m.modelName);
  const metricValues = sorted.map(m => Number(m[metricKey] || 0).toFixed(1));

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
      formatter: (params) => formatModelCardTooltip(sorted[params[0].dataIndex]?.raw)
    },
    grid: { left: '3%', right: '4%', bottom: '12%', top: '12%', containLabel: true },
    xAxis: {
      type: 'category',
      data: modelLabels,
      axisLine: { lineStyle: { color: '#cbd5e1' } },
      axisLabel: {
        color: '#64748b',
        fontFamily: 'system-ui, -apple-system, sans-serif',
        interval: 0,
        rotate: 45,
        fontSize: 10,
        lineHeight: 13,
        formatter: (value) => formatChartModelLabel(value)
      }
    },
    yAxis: {
      type: 'value',
      name: yAxisName,
      axisLine: { lineStyle: { color: '#cbd5e1' } },
      splitLine: { lineStyle: { color: '#f1f5f9' } },
      axisLabel: { color: '#64748b', fontFamily: 'system-ui, -apple-system, sans-serif' }
    },
    series: [{
      name: yAxisName,
      type: 'bar',
      data: metricValues,
      itemStyle: {
        color: new echarts.graphic.LinearGradient(0, 0, 0, 1, [
          { offset: 0, color: gradientColors[0] },
          { offset: 1, color: gradientColors[1] }
        ]),
        borderRadius: [6, 6, 0, 0]
      },
      barWidth: 18,
      emphasis: { itemStyle: { color: hoverColor } }
    }]
  };

  chart.setOption(option);
  chart.off('click');
  chart.on('click', function (params) {
    const rawEval = sorted[params.dataIndex]?.raw;
    if (rawEval && rawEval.eval_id) {
      window.location.href = `./trace.html?eval_id=${encodeURIComponent(rawEval.eval_id)}`;
    }
  });
}

function renderModelSpeedChart(evaluations) {
  renderRankingBarChart('chart-model-speed', evaluations, {
    metricKey: 'taskSpeed',
    yAxisName: 'Task Speed',
    gradientColors: ['#ea580c', '#fb923c'],
    hoverColor: '#c2410c'
  });
}

function renderModelDensityChart(evaluations) {
  renderRankingBarChart('chart-model-density', evaluations, {
    metricKey: 'intelligenceDensity',
    yAxisName: 'Intelligence Density',
    gradientColors: ['#059669', '#34d399'],
    hoverColor: '#047857'
  });
}

/**
 * Renders 2 pie / donut charts comparing pi vs opencode cli:
 * 1. Problem Solving: Total Tasks Completed with full score
 * 2. Execution Speed: Total Execution Time Taken (in minutes/hours)
 */
function renderHarnessPieCharts(evaluations) {
  const harnessStats = {};

  evaluations.forEach(e => {
    let rawHarness = (e.harness || '').trim();
    let normHarness = rawHarness;
    const lower = rawHarness.toLowerCase();
    if (lower.includes('pi') && !lower.includes('opencode')) {
      normHarness = 'pi';
    } else if (lower.includes('opencode')) {
      normHarness = 'opencode cli';
    } else if (!normHarness) {
      normHarness = 'other';
    }

    if (!harnessStats[normHarness]) {
      harnessStats[normHarness] = {
        name: normHarness,
        tasksCompleted: 0,
        totalTimeSec: 0,
        evalCount: 0
      };
    }

    harnessStats[normHarness].evalCount += 1;

    const tests = Object.values(e.test_results || {});
    tests.forEach(t => {
      const earned = Number(t.earned_score || 0);
      const max = Number(t.max_score || 0);
      const sec = Number(t.run_time_sec || 0);

      harnessStats[normHarness].totalTimeSec += sec;
      if (max > 0 && earned >= max) {
        harnessStats[normHarness].tasksCompleted += 1;
      }
    });
  });

  const harnessList = Object.values(harnessStats);
  if (harnessList.length === 0) return;

  // Distinct branded colors for harnesses
  const harnessColors = {
    'pi': '#f97316',          // Warm vibrant orange
    'opencode cli': '#0284c7' // Modern electric cyan / blue
  };

  function getColor(harnessName) {
    return harnessColors[harnessName] || stringToColor(harnessName);
  }

  // 1. Tasks Completed Pie Chart
  const tasksChartEl = document.getElementById('chart-harness-tasks');
  const tasksChart = getOrCreateChart(tasksChartEl);
  if (tasksChart) {
    const tasksData = harnessList.map(h => ({
      name: h.name,
      value: h.tasksCompleted,
      itemStyle: { color: getColor(h.name) }
    }));

    const totalTasks = tasksData.reduce((sum, d) => sum + d.value, 0);

    const tasksOption = {
      backgroundColor: 'transparent',
      tooltip: {
        trigger: 'item',
        backgroundColor: 'rgba(255, 255, 255, 0.96)',
        borderColor: '#e2e8f0',
        shadowBlur: 12,
        shadowColor: 'rgba(0, 0, 0, 0.08)',
        textStyle: { color: '#0f172a', fontFamily: 'system-ui, -apple-system, sans-serif' },
        formatter: (params) => {
          const pct = totalTasks > 0 ? ((params.value / totalTasks) * 100).toFixed(1) : '0.0';
          return `
            <div style="font-weight:600; color:#0f172a; margin-bottom:4px;">${escapeHtml(params.name)}</div>
            <div style="font-size:0.85rem; color:#64748b;">
              Tasks Completed: <strong style="color:#0f172a;">${params.value}</strong> (${pct}%)
            </div>
          `;
        }
      },
      series: [
        {
          name: 'Tasks Completed',
          type: 'pie',
          radius: ['45%', '72%'],
          center: ['50%', '50%'],
          avoidLabelOverlap: true,
          itemStyle: {
            borderRadius: 8,
            borderColor: '#ffffff',
            borderWidth: 2
          },
          label: {
            show: true,
            formatter: '{b}\n{c} ({d}%)',
            fontFamily: 'system-ui, -apple-system, sans-serif',
            fontSize: 12,
            fontWeight: 500,
            color: '#334155'
          },
          emphasis: {
            scale: true,
            scaleSize: 8,
            label: {
              show: true,
              fontSize: 14,
              fontWeight: 700
            }
          },
          data: tasksData
        }
      ]
    };

    tasksChart.setOption(tasksOption, true);
  }

  // 2. Total Time Taken Pie Chart
  const timeChartEl = document.getElementById('chart-harness-time');
  const timeChart = getOrCreateChart(timeChartEl);
  if (timeChart) {
    const timeData = harnessList.map(h => {
      const mins = Math.round(h.totalTimeSec / 60);
      const hours = (h.totalTimeSec / 3600).toFixed(1);
      return {
        name: h.name,
        value: mins,
        hours: hours,
        itemStyle: { color: getColor(h.name) }
      };
    });

    const totalMins = timeData.reduce((sum, d) => sum + d.value, 0);

    const timeOption = {
      backgroundColor: 'transparent',
      tooltip: {
        trigger: 'item',
        backgroundColor: 'rgba(255, 255, 255, 0.96)',
        borderColor: '#e2e8f0',
        shadowBlur: 12,
        shadowColor: 'rgba(0, 0, 0, 0.08)',
        textStyle: { color: '#0f172a', fontFamily: 'system-ui, -apple-system, sans-serif' },
        formatter: (params) => {
          const pct = totalMins > 0 ? ((params.value / totalMins) * 100).toFixed(1) : '0.0';
          const hrs = params.data.hours || (params.value / 60).toFixed(1);
          return `
            <div style="font-weight:600; color:#0f172a; margin-bottom:4px;">${escapeHtml(params.name)}</div>
            <div style="font-size:0.85rem; color:#64748b;">
              Total Time: <strong style="color:#0f172a;">${params.value.toLocaleString()} min</strong> (~${hrs} hrs) (${pct}%)
            </div>
          `;
        }
      },
      series: [
        {
          name: 'Total Time Taken',
          type: 'pie',
          radius: ['45%', '72%'],
          center: ['50%', '50%'],
          avoidLabelOverlap: true,
          itemStyle: {
            borderRadius: 8,
            borderColor: '#ffffff',
            borderWidth: 2
          },
          label: {
            show: true,
            formatter: (params) => `${params.name}\n${params.value} min (${params.percent}%)`,
            fontFamily: 'system-ui, -apple-system, sans-serif',
            fontSize: 12,
            fontWeight: 500,
            color: '#334155'
          },
          emphasis: {
            scale: true,
            scaleSize: 8,
            label: {
              show: true,
              fontSize: 14,
              fontWeight: 700
            }
          },
          data: timeData
        }
      ]
    };

    timeChart.setOption(timeOption, true);
  }
}

/**
 * Converts HSL values to a 6-digit Hex color (#rrggbb).
 */
function hslToHex(h, s, l) {
  l /= 100;
  const a = (s * Math.min(l, 1 - l)) / 100;
  const f = n => {
    const k = (n + h / 30) % 12;
    const color = l - a * Math.max(Math.min(k - 3, 9 - k, 1), -1);
    return Math.round(255 * color).toString(16).padStart(2, '0');
  };
  return `#${f(0)}${f(8)}${f(4)}`;
}

/**
 * Converts a Hex color string (#rrggbb) to rgba(r, g, b, alpha).
 */
function hexToRgba(hex, alpha = 0.35) {
  if (!hex || hex[0] !== '#') return `rgba(100, 116, 139, ${alpha})`;
  const r = parseInt(hex.slice(1, 3), 16) || 0;
  const g = parseInt(hex.slice(3, 5), 16) || 0;
  const b = parseInt(hex.slice(5, 7), 16) || 0;
  return `rgba(${r}, ${g}, ${b}, ${alpha})`;
}

/**
 * Smart deterministic string-to-color hashing algorithm.
 * Uses FNV-1a with Murmur3 fmix32 avalanche finalizer to map any arbitrary dynamic string
 * (base model or harness) to a distinct, vibrant, and visually pleasing HSL/Hex color.
 */
function stringToColor(str) {
  if (!str) return '#64748b';
  const s = String(str).trim().toLowerCase();

  let h = 2166136261;
  for (let i = 0; i < s.length; i++) {
    h ^= s.charCodeAt(i);
    h = Math.imul(h, 16777619);
  }

  // Murmur3 32-bit avalanche mixing finalizer
  h ^= h >>> 16;
  h = Math.imul(h, 0x85ebca6b);
  h ^= h >>> 13;
  h = Math.imul(h, 0xc2b2ae35);
  h ^= h >>> 16;
  h = h >>> 0;

  const hue = h % 360;
  const sat = 70 + ((h >>> 8) % 15); // 70% - 84%
  const light = 46 + ((h >>> 16) % 8); // 46% - 53%

  return hslToHex(hue, sat, light);
}

function renderTopScatterChart(evaluations, viewMode = 'time', colorMode = 'base_model') {
  const chartEl = document.getElementById('chart-top-scatter');
  const chart = getOrCreateChart(chartEl);
  if (!chart) return;

  // 1. Prepare raw data points
  const rawPoints = [];
  evaluations.forEach((e, idx) => {
    const m = extractEvalMetrics(e);
    const yVal = m.intelligence;
    const xVal = viewMode === 'time' ? m.timeMin : m.runMemGb;
    if (xVal !== undefined && yVal !== undefined) {
      rawPoints.push({
        id: idx,
        x: xVal,
        y: yVal,
        name: m.modelName,
        baseModel: m.baseModel,
        harnessName: m.harnessName,
        rawEval: e
      });
    }
  });

  if (rawPoints.length === 0) return;

  // 2. Group points dynamically by colorMode (base_model or harness)
  const groupsMap = new Map();
  rawPoints.forEach(p => {
    const groupKey = (colorMode === 'harness'
      ? (p.harnessName || 'Unknown')
      : (p.baseModel || 'Unknown')
    ).trim();

    if (!groupsMap.has(groupKey)) {
      groupsMap.set(groupKey, []);
    }
    groupsMap.get(groupKey).push({
      name: p.name,
      value: [p.x, p.y],
      rawEval: p.rawEval
    });
  });

  const sortedGroupKeys = Array.from(groupsMap.keys());

  // 3. Build series configuration for each group
  const series = sortedGroupKeys.map(groupName => {
    const color = stringToColor(groupName);
    const shadow = hexToRgba(color, 0.35);
    const data = groupsMap.get(groupName);

    return {
      name: groupName,
      type: 'scatter',
      symbolSize: 11,
      data: data,
      itemStyle: {
        color: color,
        borderWidth: 1,
        borderColor: 'rgba(255, 255, 255, 0.9)',
        shadowColor: shadow,
        shadowBlur: 6
      },
      emphasis: {
        focus: 'none',
        scale: 1.6,
        itemStyle: {
          borderColor: '#ffffff',
          borderWidth: 2,
          shadowBlur: 16,
          shadowColor: color,
          opacity: 1
        }
      },
      blur: {
        itemStyle: {
          opacity: 0.15,
          shadowBlur: 0
        }
      }
    };
  });

  const xAxisName = viewMode === 'time' ? 'Total Completion Time (min)' : 'Memory / VRAM Size (GB)';

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
      formatter: (params) => formatModelCardTooltip(params.data?.rawEval)
    },
    legend: {
      type: 'scroll',
      orient: 'vertical',
      data: sortedGroupKeys,
      top: 'middle',
      right: 12,
      textStyle: { color: '#64748b', fontFamily: 'system-ui, -apple-system, sans-serif', fontSize: 12 },
      itemWidth: 10,
      itemHeight: 10,
      icon: 'circle',
      pageIconColor: '#ea580c',
      pageIconInactiveColor: '#cbd5e1',
      pageTextStyle: { color: '#64748b' }
    },
    grid: { left: '4%', right: 260, bottom: '12%', top: '8%', containLabel: true },
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
    series: series
  };

  chart.setOption(option, true);

  // Helper to compute exact identity key: model / harness / reasoning / kv_quant
  function getExactConfigKey(rawEval) {
    if (!rawEval) return '';
    const name = String(rawEval.name || rawEval.model || '').trim();
    const harness = String(rawEval.harness || rawEval.harness_name || '').trim();
    const reasoning = String(rawEval.reasoning || '').trim();
    const kv = String(rawEval.kv_quant || '').trim();
    return `${name}:::${harness}:::${reasoning}:::${kv}`;
  }

  // Hover highlighting: only highlight dots matching exact model/harness/reasoning/kv_quant, dim all others
  chart.off('mouseover');
  chart.on('mouseover', function (params) {
    if (!params.data || !params.data.rawEval) return;
    const targetKey = getExactConfigKey(params.data.rawEval);
    if (!targetKey) return;

    // Collect all data points across all series with exact same configuration
    const matchPoints = [];
    series.forEach((s, sIdx) => {
      s.data.forEach((d, dIdx) => {
        if (getExactConfigKey(d.rawEval) === targetKey) {
          matchPoints.push({ seriesIndex: sIdx, dataIndex: dIdx });
        }
      });
    });

    if (matchPoints.length > 0) {
      chart.dispatchAction({
        type: 'highlight',
        batch: matchPoints
      });
    }
  });

  chart.off('mouseout');
  chart.on('mouseout', function () {
    chart.dispatchAction({
      type: 'downplay'
    });
  });

  chart.off('click');
  chart.on('click', function (params) {
    if (params.data && params.data.rawEval && params.data.rawEval.eval_id) {
      window.location.href = `./trace.html?eval_id=${encodeURIComponent(params.data.rawEval.eval_id)}`;
    }
  });

  // Track selected legend items set (null means default: all items visible)
  let activeSelectedSet = null;

  chart.off('legendselectchanged');
  chart.on('legendselectchanged', function (params) {
    const clickedName = params.name;

    if (!activeSelectedSet) {
      // Nothing was soloed yet (all were visible): solo this clicked item
      activeSelectedSet = new Set([clickedName]);
    } else if (activeSelectedSet.has(clickedName)) {
      // If currently active and clicked again: toggle it off
      activeSelectedSet.delete(clickedName);
      // If none left selected, restore all to visible
      if (activeSelectedSet.size === 0) {
        activeSelectedSet = null;
      }
    } else {
      // Add this new item to the active visible subset (both/all shown)
      activeSelectedSet.add(clickedName);
    }

    const selected = {};
    sortedGroupKeys.forEach(k => {
      selected[k] = activeSelectedSet ? activeSelectedSet.has(k) : true;
    });

    chart.setOption({
      legend: { selected: selected }
    });
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
      const evalId = m.eval_id || '';
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
      <span style="display:inline-block; padding:2px 8px; border-radius:10px; font-family:var(--font-mono, monospace); font-size:0.75rem; font-weight:600; background:#f1f5f9; color:#64748b; border:1px solid #cbd5e1;">${escapeHtml(m.harnessName)}</span>
    </div>

    <div style="min-width: 210px;">
      ${row('Intelligence', m.intelligence)}
      ${row('Completion Time', m.timeMin + ' min')}
      ${row('Memory Use', m.memoryGb + ' GB')}
      ${row('Speculative', m.speculativeDecoding)}
      ${row('Reasoning', m.reasoning)}
      ${row('KV Cache Quant', m.kvQuant)}
    </div>
  `;
}
