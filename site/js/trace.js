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
  const evalIdParam = urlParams.get('eval_id') || urlParams.get('id');
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
        const testsObj = currentTraceData.tests || {};
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
  fetch(`./results/${encodeURIComponent(evalId)}/full_trace.json`)
    .then(res => {
      if (!res.ok) throw new Error(`Evaluation trace '${evalId}' not found in results directory.`);
      return res.json();
    })
    .then(traceData => {
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
      renderTracePage(parsed);
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

  const modelName = data.name || data.model || 'Evaluation Run';
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
  const benchDate = data.benchmark_date || (data.start_time ? data.start_time.split('T')[0] : '');
  const intelligence = (data.intelligence !== undefined && data.intelligence !== null && !isNaN(Number(data.intelligence))) ? `${Number(data.intelligence).toFixed(1)}%` : 'N/A';
  const taskSpeed = (data.task_speed !== undefined && data.task_speed !== null && !isNaN(Number(data.task_speed))) ? `${Number(data.task_speed).toFixed(1)} tasks/hr` : 'N/A';
  const intelDensity = (data.intelligence_density !== undefined && data.intelligence_density !== null && !isNaN(Number(data.intelligence_density))) ? `${Number(data.intelligence_density).toFixed(1)} tasks/GB` : 'N/A';
  const launchCfg = data.launch_config || '';

  const testsObj = data.tests || {};
  const testsList = Object.values(testsObj);
  const totalTimeSec = testsList.reduce((acc, t) => acc + (t.duration_seconds || 0), 0);

  const completionTimeDisplay = totalTimeSec > 0
    ? `${(totalTimeSec / 60).toFixed(1)} min`
    : 'N/A';

  document.title = `${modelName} x ${harness} | Evaluation Trace`;

  container.innerHTML = `
    <div class="summary-title-group">
      <h1>
        <span>${escapeHtml(modelName)}<span class="title-separator">x</span><span class="title-harness">${escapeHtml(harness)}</span></span>
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

    <div class="summary-kpi-grid">
      <div class="kpi-card">
        <span class="kpi-label">Intelligence Pass Rate</span>
        <span class="kpi-value" style="color: #16a34a;">${intelligence}</span>
        <span class="kpi-sub">Overall benchmark completion</span>
      </div>
      <div class="kpi-card">
        <span class="kpi-label">Completion Time</span>
        <span class="kpi-value" style="color: #2563eb;">${completionTimeDisplay}</span>
        <span class="kpi-sub">Total execution duration</span>
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

    ${launchCfg ? `
      <div class="launch-config-box">
        <code>${escapeHtml(launchCfg)}</code>
      </div>
    ` : ''}
  `;
}

function renderTestTabsAndTimeline(data, preferredTestKey) {
  const tabsContainer = document.getElementById('test-tabs-container');
  const testsObj = data.tests || {};
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
    const durSec = step.duration_seconds !== undefined ? `${Math.round(Number(step.duration_seconds))}s` : '';
    const events = getStepEvents(step);
    const hasTools = events.some(e => e.type === 'tool');
    const hasReasoning = events.some(e => e.type === 'reasoning');
    const checks = (step.evaluation && step.evaluation.check_results) || [];

    const contextUsedPct = getStepContextUsedPct(step);
    const contextDisplay = (contextUsedPct !== null && contextUsedPct !== undefined && contextUsedPct > 0)
      ? `<span class="step-tokens">Context Used: ${Math.round(contextUsedPct)}%</span>`
      : '';
    const isCollapsed = currentViewMode === 'simple';

    return `
      <article class="step-card ${isPassed ? 'passed' : 'failed'} ${isCollapsed ? 'collapsed' : ''}" data-has-tool="${hasTools}" data-has-reasoning="${hasReasoning}" data-passed="${isPassed}">
        <!-- Step Header Bar -->
        <header class="step-header">
          <div class="step-header-left">
            <span class="step-number-tag">Step ${idx + 1}/${steps.length}</span>
            ${durSec ? `<span class="step-duration"><span class="step-clock-icon">⏱</span> ${durSec}</span>` : ''}
            ${contextDisplay}
          </div>

          <div class="step-header-right">
            <span class="badge ${isPassed ? 'badge-success' : 'badge-failed'}">
              ${isPassed ? '✓ Passed' : '✗ Failed'}
            </span>
            <svg class="step-chevron" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2">
              <polyline points="6 9 12 15 18 9"></polyline>
            </svg>
          </div>
        </header>

        <!-- Step Body (3-Column Layout: Col 1 Icon, Col 2 Content, Col 3 Evaluation Results) -->
        <div class="step-body">
          <div class="step-columns-wrapper">
            <!-- Trace Stream (Col 1 Icons & Col 2 Content) -->
            <div class="step-trace-stream">
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
              </div>
            </div>

            <!-- Evaluation Results Sidebar (Col 3) -->
            <aside class="step-eval-column">
              <div class="step-eval-panel ${isPassed ? 'eval-panel-pass' : 'eval-panel-fail'}">
                <div class="eval-panel-header">
                  <div class="eval-panel-title">
                    <span class="eval-status-icon ${isPassed ? 'passed' : 'failed'}">${isPassed ? '✓' : '✗'}</span>
                    <span>Evaluation</span>
                  </div>
                  <span class="eval-score-badge ${isPassed ? 'badge-success' : 'badge-failed'}">
                    ${scoreText}
                  </span>
                </div>

                <div class="eval-panel-body">
                  ${checks.length > 0 ? `
                    <div class="eval-assertions-summary">
                      Procedural Assertions: <strong>${checks.filter(c => c.passed).length}/${checks.length} Passed</strong>
                    </div>
                    <div class="validation-items">
                      ${checks.map(c => `
                        <div class="assertion-item">
                          <span class="assertion-icon ${c.passed ? 'passed' : 'failed'}">${c.passed ? '✓' : '✗'}</span>
                          <span class="assertion-msg">${escapeHtml(c.message || '')}</span>
                        </div>
                      `).join('')}
                    </div>
                  ` : `
                    <div class="eval-empty-note">
                      ${isPassed ? 'All step criteria satisfied' : 'Step did not meet passing criteria'}
                    </div>
                  `}
                </div>
              </div>
            </aside>
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
 * Extract normalized chronological event list for a step.
 * Uses step.events directly if rich/interspersed, or reconstructs chronological sequence from step.messages if needed.
 */
function getStepEvents(step) {
  const rawEvents = step.events || [];
  const messages = step.messages || [];

  // Check if events is lumped (e.g. single reasoning event followed by tools while messages has multi-turn history)
  const reasoningCount = rawEvents.filter(e => e.type === 'reasoning').length;
  const isLumped = (reasoningCount <= 1 && messages.length >= 2);

  if ((isLumped || rawEvents.length === 0) && messages.length > 0) {
    const toolResults = {};
    messages.forEach(msg => {
      const content = msg.content || [];
      if (Array.isArray(content)) {
        content.forEach(part => {
          if (part && part.type === 'toolResult') {
            const cid = part.toolCallId;
            if (cid) {
              let textOut = '';
              (part.content || []).forEach(c => {
                if (c && c.type === 'text') textOut += c.text || '';
              });
              toolResults[cid] = {
                output: textOut,
                isError: Boolean(part.isError)
              };
            }
          }
        });
      }
    });

    const reconstructed = [];
    messages.forEach(msg => {
      const role = msg.role;
      const content = msg.content;
      if (typeof content === 'string' && role === 'assistant') {
        if (content.trim()) {
          reconstructed.push({ type: 'response', content: content.trim() });
        }
      } else if (Array.isArray(content)) {
        content.forEach(part => {
          if (!part) return;
          if (part.type === 'thinking' && part.thinking && part.thinking.trim()) {
            reconstructed.push({ type: 'reasoning', content: part.thinking.trim() });
          } else if (part.type === 'text' && part.text && part.text.trim() && role === 'assistant') {
            reconstructed.push({ type: 'response', content: part.text.trim() });
          } else if (part.type === 'toolCall') {
            const cid = part.id;
            const res = toolResults[cid] || {};
            const tc = {
              tool: part.name || 'tool',
              call_id: cid,
              input: part.arguments,
              output: res.output || '',
              status: res.isError ? 'error' : 'completed',
              exit_code: res.isError ? 1 : 0
            };
            reconstructed.push({ type: 'tool', data: tc });
          }
        });
      }
    });

    if (reconstructed.length > 0) {
      return reconstructed;
    }
  }

  return rawEvents;
}

/**
 * Renders events in exact chronological sequence across the 2-column layout.
 */
function renderChronologicalEvents(step, viewMode = 'simple') {
  const events = getStepEvents(step);

  if (viewMode === 'simple') {
    const trailChips = [];
    events.forEach(ev => {
      if (ev.type === 'reasoning') {
        trailChips.push('<span class="breadcrumb-chip chip-thinking">Thinking</span>');
      } else if (ev.type === 'tool') {
        const tc = ev.data || {};
        trailChips.push(`<span class="breadcrumb-chip chip-tool">Tool: ${escapeHtml(capitalize(tc.tool || 'tool'))}</span>`);
      }
    });

    const lastResponse = [...events].reverse().find(e => e.type === 'response');

    return `
      ${trailChips.length > 0 ? `
        <div class="grid-table-row row-breadcrumbs">
          <div class="col-type">
            <span class="type-pill pill-tool" title="Execution Flow">⚙️</span>
          </div>
          <div class="col-content">
            <div class="execution-breadcrumb-trail">${trailChips.join('')}</div>
          </div>
        </div>
      ` : ''}
      ${lastResponse ? `
        <div class="grid-table-row row-response">
          <div class="col-type">
            <span class="type-pill pill-response" title="Final Response">💬</span>
          </div>
          <div class="col-content">
            <div class="response-text">${escapeHtml(lastResponse.content || '')}</div>
          </div>
        </div>
      ` : ''}
    `;
  }

  // Full View Mode: Render each event in exact sequential order
  return events.map(ev => {
    if (ev.type === 'reasoning') {
      return `
        <div class="grid-table-row row-thinking">
          <div class="col-type">
            <span class="type-pill pill-thinking" title="Model Thinking">🧠</span>
          </div>
          <div class="col-content">
            <div class="stack-block block-thinking is-only">
              <span class="thinking-badge-corner">Thinking</span>
              <div class="reasoning-stream">${escapeHtml(ev.content || '')}</div>
            </div>
          </div>
        </div>
      `;
    }

    if (ev.type === 'tool') {
      const tc = ev.data || {};
      return `
        <div class="grid-table-row row-tool">
          <div class="col-type">
            <span class="type-pill pill-tool" title="Tool Execution">⚙️</span>
          </div>
          <div class="col-content">
            <div class="stack-block block-tool is-only">
              <span class="tool-badge-corner">Tool: ${escapeHtml(tc.tool || 'tool')}</span>
              <pre class="tool-code-preview"><code>${escapeHtml(formatToolInputOutput(tc))}</code></pre>
            </div>
          </div>
        </div>
      `;
    }

    if (ev.type === 'response') {
      return `
        <div class="grid-table-row row-response">
          <div class="col-type">
            <span class="type-pill pill-response" title="Assistant Response">💬</span>
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

function formatToolInputOutput(tc) {
  let out = '';
  if (tc.input) {
    out += `Input:\n${typeof tc.input === 'string' ? tc.input : JSON.stringify(tc.input, null, 2)}\n`;
  }
  if (tc.output) {
    out += `\nOutput:\n${typeof tc.output === 'string' ? tc.output : JSON.stringify(tc.output, null, 2)}`;
  }
  return out.trim() || 'Executed';
}


function getStepTokens(step) {
  return { in: step.tokens_in || 0, out: step.tokens_out || 0 };
}

function getStepContextUsedPct(step) {
  if (step && step.context_used_pct !== undefined && step.context_used_pct !== null) {
    return Number(step.context_used_pct);
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

