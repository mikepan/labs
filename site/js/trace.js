/**
 * trace.js - Client-side live parser and interactive timeline renderer.
 * 
 * Supports:
 * - Dynamic URL parameter resolution (?trace_id=... or ?file=...)
 * - Fallback lookup in site/data/benchmark-data.json
 * - Local file drag-and-drop / file upload for full_trace.json / results.json
 * - Live parsing with zero prerendering
 * - Horizontal multi-column layout for chat turns, thinking, tools, and assertions
 */

let currentTraceData = null;
let activeTestKey = null;
let currentViewMode = 'simple';

document.addEventListener('DOMContentLoaded', () => {
  const urlParams = new URLSearchParams(window.location.search);
  const evalIdParam = urlParams.get('eval_id') || urlParams.get('trace_id') || urlParams.get('id');
  const fileParam = urlParams.get('file');

  // Wire Simple / Full mode toggles
  const modePills = document.querySelectorAll('.filter-pill');
  modePills.forEach(pill => {
    pill.addEventListener('click', () => {
      modePills.forEach(p => p.classList.remove('active'));
      pill.classList.add('active');
      const radio = pill.querySelector('input[type="radio"]');
      if (radio) radio.checked = true;
      currentViewMode = pill.getAttribute('data-mode') || 'simple';
      if (currentTraceData) {
        const testsObj = currentTraceData.tests || (currentTraceData.suite_trace ? currentTraceData.suite_trace.tests : {}) || currentTraceData.test_results || {};
        if (activeTestKey && testsObj[activeTestKey]) {
          renderTestStepsTimeline(testsObj[activeTestKey]);
        }
      }
    });
  });

  // Resolve and load data
  if (fileParam) {
    loadJsonFromUrl(fileParam);
  } else if (evalIdParam) {
    loadTraceById(evalIdParam);
  } else {
    showError('No Evaluation Specified', 'Select a model run from the dashboard to inspect its trace.');
  }
});

function showLoading() {
  document.getElementById('loading-state').style.display = 'flex';
  document.getElementById('error-state').style.display = 'none';
  document.getElementById('trace-content').style.display = 'none';
}

function showError(title, message) {
  document.getElementById('loading-state').style.display = 'none';
  document.getElementById('error-state').style.display = 'flex';
  document.getElementById('trace-content').style.display = 'none';
  document.getElementById('error-title').textContent = title || 'Trace Not Found';
  document.getElementById('error-message').textContent = message || 'Could not locate evaluation data.';
}

function showContent() {
  document.getElementById('loading-state').style.display = 'none';
  document.getElementById('error-state').style.display = 'none';
  document.getElementById('trace-content').style.display = 'block';
}

function loadJsonFromUrl(url) {
  showLoading();
  fetch(url)
    .then(res => {
      if (!res.ok) throw new Error(`HTTP error ${res.status}`);
      return res.json();
    })
    .then(data => {
      renderTracePage(data);
    })
    .catch(err => {
      showError('Failed to Load File', `Could not fetch JSON from '${url}': ${err.message}`);
    });
}

function loadTraceById(evalId) {
  showLoading();
  Promise.all([
    fetch(`./results/${encodeURIComponent(evalId)}/full_trace.json`).then(res => {
      if (!res.ok) throw new Error(`Evaluation trace '${evalId}' not found in results directory.`);
      return res.json();
    }),
    fetch(`./results/benchmark-data.json`).then(r => r.ok ? r.json() : null).catch(() => null)
  ])
    .then(([traceData, benchData]) => {
      if (benchData && benchData.evaluations) {
        const match = benchData.evaluations.find(e => e.eval_id === evalId);
        if (match) {
          traceData.company = traceData.company || match.company;
          traceData.base_model = traceData.base_model || match.base_model;
          traceData.harness = traceData.harness || match.harness;
          traceData.harness_version = traceData.harness_version || match.harness_version;
          traceData.memory_gb = traceData.memory_gb || match.memory_gb;
          traceData.kv_quant = traceData.kv_quant || match.kv_quant;
          traceData.reasoning = traceData.reasoning || match.reasoning;
          traceData.intelligence = traceData.intelligence !== undefined ? traceData.intelligence : match.intelligence;
          traceData.task_speed = traceData.task_speed !== undefined ? traceData.task_speed : match.task_speed;
          traceData.intelligence_density = traceData.intelligence_density !== undefined ? traceData.intelligence_density : match.intelligence_density;
          traceData.launch_config = traceData.launch_config || match.launch_config;
        }
      }
      renderTracePage(traceData);
    })
    .catch(err => {
      showError('Evaluation Not Found', err.message);
    });
}




function handleLocalFileUpload(event) {
  const file = event.target.files[0];
  if (!file) return;

  showLoading();
  const reader = new FileReader();
  reader.onload = (e) => {
    try {
      const parsed = JSON.parse(e.target.result);

      // If it's a full_trace.json format containing suite_trace, flatten it
      let normalized = parsed;
      if (parsed.suite_trace) {
        normalized = {
          ...parsed,
          tests: parsed.suite_trace.tests || {},
          start_time: parsed.suite_trace.start_time,
          end_time: parsed.suite_trace.end_time,
        };
      }
      renderTracePage(normalized);
    } catch (err) {
      showError('Invalid JSON Format', `Failed to parse uploaded JSON file: ${err.message}`);
    }
  };
  reader.onerror = () => {
    showError('Read Error', 'Could not read local file.');
  };
  reader.readAsText(file);
}

/**
 * Normalizes different formats (results.json, full_trace.json, benchmark-data.json row) into a unified object model.
 */
function normalizeBenchmarkRowToTrace(row, columns) {
  // Map array row by index
  const modelName = row[0] || 'Unknown Model';
  const company = row[1] || '';
  const parentModel = row[2] || modelName;
  const kvQuant = row[3] || 'FP16';
  const contextLength = row[4] || 262144;
  const memoryGb = row[5] || 0;
  const startTime = row[6] || new Date().toISOString();
  const llmServer = row[7] || 'vLLM';
  const speculative = row[8] || 'off';
  const harness = row[9] || 'opencode';
  const harnessVersion = row[10] || '1.18.18';
  const reasoning = row[11] || 'off';
  const launchConfig = row[12] || '';
  const intelligence = row[13] || 0;
  const taskSpeed = row[14] || 0;
  const intelDensity = row[15] || 0;
  const testResults = row[16] || {};

  // Build test suite trace objects
  const tests = {};
  for (const [testKey, tData] of Object.entries(testResults)) {
    tests[testKey] = {
      name: tData.name || testKey,
      trace_id: tData.trace_id,
      earned_score: tData.earned_score,
      max_score: tData.max_score,
      duration_seconds: tData.run_time_sec,
      tokens_in: tData.tokens_in,
      tokens_out: tData.tokens_out,
      context_used_pct: tData.context_used_pct,
      steps: tData.steps || [],
    };
  }

  return {
    model: modelName,
    company: company,
    base_model: parentModel,
    kv_quant: kvQuant,
    context_length: contextLength,
    memory_gb: memoryGb,
    start_time: startTime,
    llm_server: llmServer,
    speculative_decoding: speculative,
    harness: harness,
    harness_version: harnessVersion,
    reasoning: reasoning,
    launch_config: launchConfig,
    intelligence: intelligence,
    task_speed: taskSpeed,
    intelligence_density: intelDensity,
    tests: tests,
  };
}

/**
 * Main Render Controller: Renders executive summary + timeline
 */
function renderTracePage(traceData, preferredTestKey) {
  currentTraceData = traceData;
  showContent();

  renderExecutiveSummary(traceData);
  renderTestTabsAndTimeline(traceData, preferredTestKey);
}

function renderExecutiveSummary(data) {
  const container = document.getElementById('executive-summary');

  const modelName = data.model || data.eval_name || data.name || 'Evaluation Run';
  const company = data.company || '';
  const baseModel = data.base_model || '';
  const llmDisplay = (company && baseModel) ? `${company}/${baseModel}` : (baseModel || company || modelName);
  const harness = data.harness || 'opencode';
  const harnessVer = data.harness_version || '';
  const llmServer = data.llm_server || 'vLLM';
  const specDecoding = data.speculative_decoding || 'off';
  const reasoning = data.reasoning || 'off';
  const kvQuant = data.kv_quant || 'FP16';
  const memGb = data.memory_gb ? `${data.memory_gb} GB` : 'N/A';
  const contextLength = data.context_length;
  const contextDisplay = contextLength ? (contextLength >= 1000 ? `${Math.round(contextLength / 1000)}k` : `${contextLength}`) : '';
  const benchDate = data.benchmark_date || (data.start_time ? data.start_time.split('T')[0] : (data.timestamp ? data.timestamp.split('T')[0] : ''));
  const intelligence = (data.intelligence !== undefined && data.intelligence !== null && !isNaN(Number(data.intelligence))) ? `${Number(data.intelligence).toFixed(1)}%` : 'N/A';
  const taskSpeed = (data.task_speed !== undefined && data.task_speed !== null && !isNaN(Number(data.task_speed))) ? `${Number(data.task_speed).toFixed(1)} tasks/hr` : 'N/A';
  const intelDensity = (data.intelligence_density !== undefined && data.intelligence_density !== null && !isNaN(Number(data.intelligence_density))) ? `${Number(data.intelligence_density).toFixed(1)} tasks/GB` : 'N/A';
  const launchCfg = data.launch_config || '';

  container.innerHTML = `
    <div class="summary-header-row">
      <div class="summary-title-group">
        <h1>
          <span>${escapeHtml(modelName)}</span>
        </h1>
        <div class="summary-meta-badges">
          <span class="badge">LLM: ${escapeHtml(llmDisplay)}</span>
          <span class="badge">Server: ${escapeHtml(llmServer)}</span>
          <span class="badge">Spec Decoding: ${escapeHtml(specDecoding)}</span>
          <span class="badge">Reasoning: ${escapeHtml(reasoning)}</span>
          <span class="badge">KV Cache: ${escapeHtml(kvQuant)}</span>
          ${contextDisplay ? `<span class="badge">Context: ${escapeHtml(contextDisplay)}</span>` : ''}
          <span class="badge">Harness: ${escapeHtml(harness)}${harnessVer ? ' ' + escapeHtml(harnessVer) : ''}</span>
          ${benchDate ? `<span class="badge">Test Date: ${escapeHtml(benchDate)}</span>` : ''}
        </div>
      </div>
    </div>

    <div class="summary-kpi-grid">
      <div class="kpi-card">
        <span class="kpi-label">Intelligence Pass Rate</span>
        <span class="kpi-value" style="color: #16a34a;">${intelligence}</span>
        <span class="kpi-sub">Overall benchmark completion</span>
      </div>
      <div class="kpi-card">
        <span class="kpi-label">Task Speed</span>
        <span class="kpi-value" style="color: var(--accent-orange, #f97316);">${taskSpeed}</span>
        <span class="kpi-sub">Turn completion rate</span>
      </div>
      <div class="kpi-card">
        <span class="kpi-label">Intelligence Density</span>
        <span class="kpi-value">${intelDensity}</span>
        <span class="kpi-sub">Pass efficiency per GB</span>
      </div>
      <div class="kpi-card">
        <span class="kpi-label">RAM / VRAM Footprint</span>
        <span class="kpi-value">${memGb}</span>
        <span class="kpi-sub">Engine memory consumption</span>
      </div>
    </div>
  `;
}

function renderTestTabsAndTimeline(data, preferredTestKey) {
  const tabsContainer = document.getElementById('test-tabs-container');
  const testsObj = data.tests || (data.suite_trace ? data.suite_trace.tests : {}) || data.test_results || {};
  const testKeys = Object.keys(testsObj);

  if (testKeys.length === 0) {
    document.getElementById('timeline-feed').innerHTML = `
      <div class="state-container">
        <p>No test step executions recorded in this trace.</p>
      </div>
    `;
    return;
  }

  activeTestKey = (preferredTestKey && testKeys.includes(preferredTestKey)) ? preferredTestKey : testKeys[0];

  // Render tab buttons
  tabsContainer.innerHTML = testKeys.map(key => {
    const tData = testsObj[key] || {};
    const label = tData.name || key;
    return `
      <button class="test-tab-btn ${key === activeTestKey ? 'active' : ''}" data-key="${key}">
        ${escapeHtml(label)}
      </button>
    `;
  }).join('');

  tabsContainer.querySelectorAll('.test-tab-btn').forEach(btn => {
    btn.addEventListener('click', () => {
      tabsContainer.querySelectorAll('.test-tab-btn').forEach(b => b.classList.remove('active'));
      btn.classList.add('active');
      activeTestKey = btn.getAttribute('data-key');
      renderTestStepsTimeline(testsObj[activeTestKey]);
    });
  });

  renderTestStepsTimeline(testsObj[activeTestKey]);
}

function renderTestStepsTimeline(testData) {
  const feed = document.getElementById('timeline-feed');
  if (!testData) {
    feed.innerHTML = `<div class="state-container"><p>Select a test suite above.</p></div>`;
    return;
  }

  const steps = testData.steps || [];
  if (steps.length === 0) {
    feed.innerHTML = `
      <div class="state-container">
        <p>No sequential steps found for this test.</p>
      </div>
    `;
    return;
  }

  feed.innerHTML = steps.map((step, idx) => {
    const isPassed = step.evaluation ? step.evaluation.passed : (step.earned_score >= step.max_score);
    const scoreText = `${step.earned_score ?? 0} / ${step.max_score ?? step.point ?? 1} pts`;
    const durSec = step.duration_seconds !== undefined ? `${Number(step.duration_seconds).toFixed(2)}s` : '';
    const events = step.events || [];
    const hasTools = events.some(e => e.type === 'tool');
    const hasReasoning = events.some(e => e.type === 'reasoning');
    const checks = (step.evaluation && step.evaluation.check_results) || [];

    const contextUsedPct = getStepContextUsedPct(step, testData, currentTraceData);
    const contextDisplay = (contextUsedPct !== null && contextUsedPct !== undefined)
      ? `<span class="step-tokens">Context Used: ${Math.round(contextUsedPct)}%</span>`
      : '';
    const isCollapsed = currentViewMode === 'simple';

    return `
      <article class="step-card ${isPassed ? 'passed' : 'failed'} ${isCollapsed ? 'collapsed' : ''}" data-has-tool="${hasTools}" data-has-reasoning="${hasReasoning}" data-passed="${isPassed}">
        <!-- Step Header Bar -->
        <header class="step-header">
          <div class="step-header-left">
            <span class="step-number-tag">[Step ${idx + 1}/${steps.length}]</span>
            <span class="badge ${isPassed ? 'badge-success' : 'badge-failed'}">
              ${isPassed ? '✓ Passed' : '✗ Failed'}
            </span>
          </div>

          <div class="step-header-right">
            ${durSec ? `<span class="step-duration">⏱ ${durSec}</span>` : ''}
            ${contextDisplay}
            <svg class="step-chevron" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2">
              <polyline points="6 9 12 15 18 9"></polyline>
            </svg>
          </div>
        </header>

        <!-- Step Body (Chronological Event Table) -->
        <div class="step-body">
          <div class="trace-grid-table">
            <!-- 1. User Prompt Row -->
            <div class="grid-table-row row-user-prompt">
              <div class="col-type">
                <span class="type-pill pill-user" title="User Prompt">👤</span>
              </div>
              <div class="col-content">
                <div class="content-text user-prompt-text">${escapeHtml(step.prompt || '')}</div>
              </div>
            </div>

            <!-- 2. Chronological Events Stream (Thinking, Tools, Agent Responses) -->
            ${renderChronologicalEvents(step, currentViewMode)}

            <!-- 3. Validation / Procedural Assertions Row -->
            ${checks.length > 0 ? `
              <div class="grid-table-row row-validation">
                <div class="col-type">
                  <span class="type-pill ${isPassed ? 'pill-valid-pass' : 'pill-valid-fail'}" title="Validation ${isPassed ? 'Passed' : 'Failed'}">
                    ${isPassed ? '✓' : '✗'}
                  </span>
                </div>
                <div class="col-content">
                  <div class="validation-summary">
                    <strong>Procedural Assertions: ${checks.filter(c => c.passed).length}/${checks.length} Passed (+${scoreText})</strong>
                    <div class="validation-items">
                      ${checks.map(c => `
                        <div class="assertion-item">
                          <span class="assertion-icon ${c.passed ? 'passed' : 'failed'}">${c.passed ? '✓' : '✗'}</span>
                          <span class="assertion-msg">${escapeHtml(c.message || '')}</span>
                        </div>
                      `).join('')}
                    </div>
                  </div>
                </div>
              </div>
            ` : ''}
          </div>
        </div>
      </article>
    `;
  }).join('');

  // Wire collapsible step headers
  document.querySelectorAll('.step-header').forEach(header => {
    header.addEventListener('click', () => {
      const card = header.closest('.step-card');
      card.classList.toggle('collapsed');
    });
  });
}

function capitalize(str) {
  if (!str) return '';
  return str.charAt(0).toUpperCase() + str.slice(1);
}

/**
 * Renders events in exact chronological sequence across the 2-column layout.
 */
function renderChronologicalEvents(step, viewMode = 'simple') {
  const rawEvents = step.events || [];

  // Normalize: if no explicit response event exists, promote the last reasoning event to response
  let events = rawEvents.map(e => ({ ...e }));
  const hasResponse = events.some(e => e.type === 'response');
  if (!hasResponse && events.length > 0) {
    for (let i = events.length - 1; i >= 0; i--) {
      if (events[i].type === 'reasoning') {
        events[i].type = 'response';
        break;
      }
    }
  }

  if (viewMode === 'full') {
    return events.map(ev => {
      if (ev.type === 'reasoning') {
        return `
          <div class="grid-table-row row-thinking">
            <div class="col-type">
              <span class="type-pill pill-thinking" title="Thinking">🧠</span>
            </div>
            <div class="col-content">
              <div class="reasoning-stream">${escapeHtml(ev.content || '')}</div>
            </div>
          </div>
        `;
      } else if (ev.type === 'tool') {
        const tc = ev.data || {};
        const toolName = tc.tool || 'tool';
        return `
          <div class="grid-table-row row-tool">
            <div class="col-type">
              <span class="type-pill pill-tool" title="Tool: ${escapeHtml(toolName)}">⚙️</span>
            </div>
            <div class="col-content">
              <div class="tool-code-wrapper">
                <span class="tool-badge-corner">Tool: ${escapeHtml(toolName)}</span>
                <pre class="tool-code-preview"><code>${escapeHtml(formatToolInputOutput(tc))}</code></pre>
              </div>
            </div>
          </div>
        `;
      } else if (ev.type === 'response') {
        return `
          <div class="grid-table-row row-response">
            <div class="col-type">
              <span class="type-pill pill-response" title="Response">💬</span>
            </div>
            <div class="col-content">
              <div class="response-text">${escapeHtml(ev.content || '')}</div>
            </div>
          </div>
        `;
      }
      return '';
    }).join('');
  }

  // Simple Mode: Collapse intermediate execution sequence into a single breadcrumb line
  let html = '';
  let pendingCrumbs = [];

  function flushBreadcrumbs() {
    if (pendingCrumbs.length === 0) return;
    const trailHtml = pendingCrumbs.map((c, i) => {
      const isLast = i === pendingCrumbs.length - 1;
      const chipClass = c.type === 'reasoning' ? 'chip-thinking' : 'chip-tool';
      const label = c.type === 'reasoning'
        ? `[Thinking]`
        : `[Tool: ${escapeHtml(capitalize(c.tool || 'tool'))}]`;
      return `
        <span class="breadcrumb-chip ${chipClass}">${label}</span>
        ${!isLast ? '<span class="breadcrumb-sep">&gt;</span>' : ''}
      `;
    }).join('');

    html += `
      <div class="grid-table-row row-breadcrumbs">
        <div class="col-type">
          <span class="type-pill pill-tool" title="Execution Sequence">⚙️</span>
        </div>
        <div class="col-content">
          <div class="execution-breadcrumb-trail">
            ${trailHtml}
          </div>
        </div>
      </div>
    `;
    pendingCrumbs = [];
  }

  events.forEach(ev => {
    if (ev.type === 'reasoning') {
      pendingCrumbs.push({ type: 'reasoning' });
    } else if (ev.type === 'tool') {
      const tc = ev.data || {};
      pendingCrumbs.push({ type: 'tool', tool: tc.tool || 'tool' });
    } else if (ev.type === 'response') {
      flushBreadcrumbs();
      html += `
        <div class="grid-table-row row-response">
          <div class="col-type">
            <span class="type-pill pill-response" title="Response">💬</span>
          </div>
          <div class="col-content">
            <div class="response-text">${escapeHtml(ev.content || '')}</div>
          </div>
        </div>
      `;
    }
  });
  flushBreadcrumbs();

  return html;
}

function formatIsoTime(isoString) {
  if (!isoString) return '--:--:--';
  try {
    const d = new Date(isoString);
    if (isNaN(d.getTime())) return '--:--:--';
    return d.toLocaleTimeString('en-US', {
      hour12: false,
      hour: '2-digit',
      minute: '2-digit',
      second: '2-digit',
      fractionalSecondDigits: 3,
    });
  } catch (e) {
    return '--:--:--';
  }
}

function formatToolInputOutput(tc) {
  let out = '';
  if (tc.input) {
    if (typeof tc.input === 'string') {
      out += `Input:\n${tc.input}\n`;
    } else {
      out += `Input:\n${JSON.stringify(tc.input, null, 2)}\n`;
    }
  }
  if (tc.output) {
    if (typeof tc.output === 'string') {
      out += `\nOutput:\n${tc.output}`;
    } else {
      out += `\nOutput:\n${JSON.stringify(tc.output, null, 2)}`;
    }
  }
  return out.trim() || 'Executed';
}


function getStepTokens(step) {
  if (step.tokens_in !== undefined && step.tokens_out !== undefined) {
    return { in: step.tokens_in || 0, out: step.tokens_out || 0 };
  }
  let tin = 0;
  let tout = 0;
  if (Array.isArray(step.messages)) {
    step.messages.forEach(msg => {
      const parts = msg.parts || [];
      let foundInParts = false;
      parts.forEach(p => {
        if (p.type === 'step-finish' && p.tokens) {
          tin += (p.tokens.input || 0);
          tout += (p.tokens.output || 0);
          foundInParts = true;
        }
      });
      if (!foundInParts && msg.info && msg.info.tokens) {
        tin += (msg.info.tokens.input || 0);
        tout += (msg.info.tokens.output || 0);
      }
    });
  }
  return { in: tin, out: tout };
}

function getStepPeakContextTokens(step) {
  let peakCtx = 0;
  if (Array.isArray(step.messages)) {
    step.messages.forEach(msg => {
      const parts = msg.parts || [];
      let foundInParts = false;
      parts.forEach(p => {
        if (p.type === 'step-finish' && p.tokens) {
          const turnCtx = (p.tokens.input || 0) + (p.tokens.output || 0);
          if (turnCtx > peakCtx) peakCtx = turnCtx;
          foundInParts = true;
        }
      });
      if (!foundInParts && msg.info && msg.info.tokens) {
        const turnCtx = (msg.info.tokens.input || 0) + (msg.info.tokens.output || 0);
        if (turnCtx > peakCtx) peakCtx = turnCtx;
      }
    });
  }
  return peakCtx;
}

function getStepContextUsedPct(step, testData, rootTraceData) {
  // 1. If step explicitly has context_used_pct
  if (step.context_used_pct !== undefined && step.context_used_pct !== null) {
    return Number(step.context_used_pct);
  }

  // 2. Compute peak context window size across individual turns in this step
  const maxContext = (rootTraceData && rootTraceData.context_length) || (testData && testData.context_length) || 262144;
  const peakContext = getStepPeakContextTokens(step);

  if (peakContext > 0 && maxContext > 0) {
    return (peakContext / maxContext) * 100;
  }

  // 3. Fallback: If no message turns exist (e.g. single turn / legacy trace), check tokens
  const tokens = getStepTokens(step);
  const totalTokens = (tokens.in || 0) + (tokens.out || 0);

  if (totalTokens > 0 && maxContext > 0) {
    return (totalTokens / maxContext) * 100;
  }

  // 4. Fallback to suite test-level context_used_pct if available
  if (testData && testData.context_used_pct !== undefined && testData.context_used_pct !== null) {
    return Number(testData.context_used_pct);
  }

  return null;
}

function escapeHtml(str) {
  if (!str) return '';
  return String(str).replace(/[&<>"']/g, function (m) {
    return {
      '&': '&amp;',
      '<': '&lt;',
      '>': '&gt;',
      '"': '&quot;',
      "'": '&#039;'
    }[m];
  });
}

