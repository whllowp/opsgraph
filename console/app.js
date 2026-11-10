"use strict";

const state = {
  data: null,
  selected: null,
  selectedAgent: null,
  selectedCode: null,
  selectedAlert: null,
  tab: "diagnosis",
  filters: { calls: true, business: true, agents: true, ops: false, observedOnly: false },
  transform: { x: 0, y: 0, k: 1 },
  search: "",
  simulation: null,
};

const $ = (selector) => document.querySelector(selector);
const $$ = (selector) => Array.from(document.querySelectorAll(selector));
const NS = "http://www.w3.org/2000/svg";

const fmt = {
  qps(value) {
    const n = Number(value || 0);
    if (n >= 1000000) return (n / 1000000).toFixed(1) + "M";
    if (n >= 1000) return (n / 1000).toFixed(1) + "k";
    return n.toFixed(0);
  },
  ms(value) {
    const n = Number(value || 0);
    if (n >= 1000) return (n / 1000).toFixed(2) + "s";
    return n.toFixed(0) + "ms";
  },
  pct(value) {
    return Number(value || 0).toFixed(1) + "%";
  },
  clock(ts) {
    if (!ts) return "--:--";
    return new Date((Number(ts) + 8 * 3600) * 1000).toISOString().slice(11, 16);
  },
  int(value) {
    return Number(value || 0).toLocaleString("en-US");
  },
};

function escapeHtml(value) {
  return String(value ?? "")
    .replaceAll("&", "&amp;")
    .replaceAll("<", "&lt;")
    .replaceAll(">", "&gt;")
    .replaceAll('"', "&quot;");
}

function initials(name) {
  const parts = String(name || "?")
    .replace(/[^A-Za-z0-9\s-]/g, " ")
    .split(/[\s-]+/)
    .filter(Boolean);
  if (parts.length >= 2) return (parts[0][0] + parts[1][0]).toUpperCase();
  return String(name || "?").slice(0, 2).toUpperCase();
}

function nodeFill(node) {
  if (node.type === "ai.agent_session") {
    if (node.status === "high") return "rgba(255,91,110,.18)";
    if (node.status === "medium") return "rgba(245,165,36,.15)";
    return "rgba(167,139,250,.16)";
  }
  if (node.type === "ops.incident") return "rgba(251,191,36,.20)";
  if (node.type === "ops.change") return "rgba(91,157,255,.15)";
  if (node.type === "ops.alert") return "rgba(245,165,36,.15)";
  if (node.type === "biz.journey") return "rgba(91,157,255,.16)";
  if (node.type === "svc.external_service") return "rgba(167,139,250,.16)";
  if (node.status === "critical") return "rgba(255,91,110,.20)";
  if (node.status === "warning") return "rgba(245,165,36,.16)";
  if (node.status === "healthy") return "rgba(45,212,191,.15)";
  return "rgba(148,163,184,.12)";
}

function nodeStroke(node) {
  if (node.type === "ai.agent_session") {
    if (node.status === "high") return "rgba(255,91,110,.9)";
    if (node.status === "medium") return "rgba(245,165,36,.8)";
    return "rgba(167,139,250,.8)";
  }
  if (node.type === "ops.incident") return "rgba(251,191,36,.75)";
  if (node.type === "biz.journey") return "rgba(91,157,255,.65)";
  if (node.type === "svc.external_service") return "rgba(167,139,250,.7)";
  if (node.status === "critical") return "rgba(255,91,110,.9)";
  if (node.status === "warning") return "rgba(245,165,36,.8)";
  if (node.status === "healthy") return "rgba(45,212,191,.72)";
  return "rgba(148,163,184,.55)";
}

function showToast(message) {
  const el = $("#toast");
  el.textContent = message;
  el.classList.add("show");
  clearTimeout(showToast.timer);
  showToast.timer = setTimeout(() => el.classList.remove("show"), 2600);
}

async function loadState() {
  try {
    const response = await fetch("/api/state", { cache: "no-store" });
    if (!response.ok) throw new Error((await response.json()).error || response.statusText);
    state.data = await response.json();
    renderAll();
    const params = new URLSearchParams(window.location.search);
    const tab = params.get("tab");
    if (tab) switchTab(tab);
    const agentKey = params.get("agent");
    if (agentKey) selectAgent(agentKey);
  } catch (error) {
    $("#empty-state").classList.remove("hidden");
    console.error(error);
  }
}

function renderAll() {
  if (!state.data) return;
  renderHeader();
  renderIncident();
  renderVerdict();
  renderCounts();
  renderBrief();
  renderRanking();
  renderFindings();
  renderRuledOut();
  renderBlast();
  renderTimeline();
  renderAgents();
  renderAlerts();
  renderCodeRca();
  renderQueries();
  renderGraph();
}

function renderHeader() {
  const meta = state.data.meta;
  $("#workspace-chip").textContent = "workspace · " + (meta.workspace || "opsgraph");
  const pill = $("#umodel-pill");
  const connected = meta.umodel && meta.umodel.connected;
  pill.classList.toggle("on", Boolean(connected));
  pill.innerHTML = `<i></i> UModel${connected ? " · " + escapeHtml(meta.umodel.provider || "connected") : " · offline"}`;
  const counts = meta.umodel_counts || meta.graph_counts || {};
  $("#top-counts").textContent = `${counts.entities ?? "—"} entities · ${counts.relations ?? "—"} relations`;
  const window = meta.window || {};
  $("#stage-sub").textContent = `${fmt.clock(window.start)}–${fmt.clock(window.end)} · service → dependency → business impact`;
  const agents = meta.agents || {};
  $("#ai-tokens").textContent = agents.total_tokens ? fmt.int(agents.total_tokens) : "—";
  $("#ai-cost").textContent = agents.cost_usd != null ? `$${Number(agents.cost_usd).toFixed(2)}` : "—";
  $("#ai-sessions").textContent = agents.sessions ?? "—";
  $("#ai-risk").textContent = agents.high_risk_effects ? `${agents.high_risk_effects} high-risk effects` : "no high-risk effects";
}

function renderIncident() {
  const incident = state.data.incident || {};
  const analysis = state.data.analysis || {};
  $("#incident-sev").textContent = incident.severity || "P1";
  $("#incident-status").textContent = incident.status || "investigating";
  $("#incident-title").textContent = incident.display_name || "Operational incident";
  $("#incident-summary").textContent = incident.summary || analysis.summary || "";
  $("#incident-victim").textContent = analysis.victim || incident.victim || "—";
  $("#incident-started").textContent = fmt.clock(incident.started_ts);
  const window = state.data.meta.window || {};
  $("#incident-window").textContent = `${fmt.clock(window.start)}–${fmt.clock(window.end)}`;
}

function renderVerdict() {
  const analysis = state.data.analysis || {};
  $("#root-name").textContent = analysis.root_cause || "—";
  const confidence = Number(analysis.confidence || 0);
  $("#root-confidence").textContent = `${Math.round(confidence * 100)}% confidence`;
  $("#confidence-fill").style.width = `${Math.round(confidence * 100)}%`;
  $("#root-summary").textContent = analysis.summary || "";
  $("#root-path").innerHTML = (analysis.path || [])
    .map((name) => `<span>${escapeHtml(name)}</span>`)
    .join('<span class="arrow">→</span>');
}

function renderCounts() {
  const types = state.data.meta.type_counts || {};
  const counts = state.data.meta.graph_counts || {};
  $("#stat-entity-sets").textContent = state.data.meta.model?.entity_sets ?? "—";
  $("#stat-links").textContent = state.data.meta.model?.links ?? "—";
  $("#stat-entities").textContent = counts.entities ?? Object.values(types).reduce((a, b) => a + b, 0);
  $("#stat-relations").textContent = counts.relations ?? "—";
  const edges = state.data.edges || [];
  $("#layer-calls").textContent = edges.filter((edge) => edge.type === "calls").length;
  $("#layer-business").textContent = edges.filter((edge) => edge.type === "depends_on").length;
  $("#layer-agents").textContent = state.data.meta.agents?.sessions ?? state.data.agents?.summary?.sessions ?? 0;
}

function renderBrief() {
  const brief = state.data.brief || {};
  if (!brief.headline) {
    document.querySelector(".brief-block")?.classList.add("hidden");
    return;
  }
  $("#brief-headline").textContent = brief.headline;
  $("#brief-meta").innerHTML = `
    <span>${escapeHtml(brief.status || "")}</span>
    <span>${escapeHtml(brief.severity || "")}</span>
    <span>${brief.next_actions?.length || 0} actions</span>
    <span>${brief.audit?.closed || 0} closed</span>
    ${brief.alerts?.groups ? `<span>${brief.alerts.groups} alert group · ${(Math.round((brief.alerts.compression_ratio || 0) * 1000) / 10)}% compressed</span>` : ""}
    ${brief.alerts?.decision ? `<span>${escapeHtml(brief.alerts.decision)} · FP ${Math.round((brief.alerts.false_alarm_probability || 0) * 100)}%</span>` : ""}`;
  $("#brief-what").textContent = brief.what || "";
  $("#brief-donot").textContent = (brief.do_not || []).join(" · ");
  $("#brief-actions").innerHTML = (brief.next_actions || [])
    .map((action) => {
      const check = action.check_result || {};
      return `
      <div class="brief-action" title="${escapeHtml(action.because || "")}">
        <span class="p">${escapeHtml(action.priority || "")}</span>
        <div>
          <strong>${escapeHtml(action.action || "")}</strong>
          <em>owner ${escapeHtml(action.owner || "")} · ${escapeHtml(action.status || "open")} · check: ${escapeHtml(check.status || "unknown")}</em>
        </div>
      </div>`;
    })
    .join("");
  const history = (brief.history || []).slice(-4).reverse();
  $("#brief-history").innerHTML = history.length
    ? `AUDIT ${history
        .map(
          (event) =>
            `<b>${escapeHtml(event.event || "")}</b> ${escapeHtml(
              (event.action || "").slice(0, 34)
            )}`
        )
        .join(" · ")}`
    : "";
  const provenance = brief.provenance || {};
  $("#brief-provenance").innerHTML = Object.entries(provenance)
    .filter(([, value]) => value && value.mode)
    .map(
      ([key, value]) =>
        `<span>${escapeHtml(key)}:${escapeHtml(value.mode || "")}</span>`
    )
    .join("");
}

function renderRanking() {
  const ranking = (state.data.analysis || {}).ranking || [];
  const max = Math.max(...ranking.map((item) => Number(item.score || 0)), 0.001);
  $("#ranking-count").textContent = `${ranking.length} candidates`;
  $("#ranking").innerHTML = ranking
    .slice(0, 6)
    .map((item) => {
      const top = item.rank === 1;
      const width = Math.max(3, (Number(item.score || 0) / max) * 100);
      const reasons = (item.reasons || [])
        .filter((reason) => reason && !reason.startsWith("no ") && !reason.startsWith("not "))
        .slice(0, 3)
        .map((reason) => `<span>${escapeHtml(reason)}</span>`)
        .join("");
      const components = item.components || {};
      return `
        <div class="rank-card ${top ? "top" : ""}" data-service="${escapeHtml(item.service)}">
          <div class="rank-head">
            <span class="rank-no">${item.rank}</span>
            <span class="rank-name">${escapeHtml(item.service)}</span>
            <span class="rank-score">${Number(item.score).toFixed(2)}</span>
          </div>
          <div class="rank-bar"><i style="width:${width}%"></i></div>
          <div class="rank-reasons">${reasons}</div>
          <div class="rank-components">
            <span>self <b>${Number(components.self_severity || 0).toFixed(2)}</b></span>
            <span>change <b>${Number(components.change_affinity || 0).toFixed(2)}</b></span>
            <span>amp <b>${Number(components.outbound_amplification || 0).toFixed(2)}</b></span>
            <span>pos <b>${Number(components.graph_position || 0).toFixed(2)}</b></span>
          </div>
        </div>`;
    })
    .join("");
  $$("#ranking .rank-card").forEach((card) => {
    card.addEventListener("click", () => selectService(card.dataset.service));
  });
}

function renderFindings() {
  const findings = (state.data.analysis || {}).findings || [];
  $("#finding-count").textContent = `${findings.length} findings`;
  $("#findings").innerHTML = findings
    .map(
      (finding) => `
      <div class="finding ${escapeHtml(finding.severity)}">
        <div class="finding-head"><span class="finding-tag">${escapeHtml(finding.severity)}</span><span class="finding-title">${escapeHtml(finding.title)}</span></div>
        <p>${escapeHtml(finding.detail)}</p>
      </div>`
    )
    .join("");
}

function renderRuledOut() {
  const items = (state.data.analysis || {}).ruled_out || [];
  $("#ruled-out").innerHTML = items
    .map(
      (item) => `
      <div class="ruled"><i></i><div><strong>${escapeHtml(item.title)}</strong><br />${escapeHtml(item.detail)}</div></div>`
    )
    .join("");
}

function renderBlast() {
  const impact = (state.data.analysis || {}).impact || {};
  const services = impact.services || [];
  $("#blast-count").textContent = `${impact.service_count || services.length} services`;
  $("#blast-summary").textContent = impact.summary || "";
  $("#blast-list").innerHTML = services
    .map(
      (item) => `
      <div class="blast-row">
        <span class="dot ${escapeHtml(item.status || "healthy")}"></span>
        <span class="name">${escapeHtml(item.service)}</span>
        <span class="role">${escapeHtml(item.role || "")}</span>
        <span class="qps">${fmt.qps(item.qps)} qps</span>
      </div>`
    )
    .join("");
  const journeys = impact.journeys || [];
  $("#journey-list").innerHTML = journeys
    .map(
      (item) => `
      <div class="journey">
        <div class="journey-head"><strong>${escapeHtml(item.journey)}</strong><em>${escapeHtml(item.tier || "")}</em></div>
        <p>${escapeHtml(item.conversion_impact || "")}<br />营收敞口 ${escapeHtml(item.revenue_exposure || "—")}</p>
      </div>`
    )
    .join("") || '<p class="muted small">没有业务旅程受影响。</p>';
}

function renderTimeline() {
  const timeline = (state.data.analysis || {}).timeline || [];
  $("#timeline").innerHTML = timeline
    .map(
      (event) => `
      <div class="tl-item ${escapeHtml(event.kind)}">
        <div class="tl-time">${escapeHtml(event.clock)}</div>
        <div class="tl-title">${escapeHtml(event.title)}</div>
        <div class="tl-detail">${escapeHtml(event.detail)}</div>
      </div>`
    )
    .join("");
}

function renderAgents() {
  const agents = state.data.agents || {};
  const sessions = agents.sessions || [];
  const summary = agents.summary || {};
  $("#agent-count").textContent = `${sessions.length} sessions`;
  $("#agent-finding-count").textContent = `${(agents.findings || []).length} findings`;
  $("#agent-summary").innerHTML = `
    <div><strong>${summary.sessions ?? sessions.length}</strong><span>sessions</span></div>
    <div><strong>${summary.tool_calls ?? 0}</strong><span>tools</span></div>
    <div><strong>${summary.failed_tool_calls ?? 0}</strong><span>failed</span></div>
    <div><strong>${summary.retry_loops ?? 0}</strong><span>loops</span></div>`;
  if (!sessions.length) {
    $("#agent-list").innerHTML = '<p class="muted small">没有 AgentSight 数据。用 <code>--agentsight snapshot.json</code> 导入。</p>';
    $("#agent-detail").innerHTML = '<p class="muted small">导入 AgentSight 快照后，这里会显示 tool → effect 证据链。</p>';
    $("#agent-findings").innerHTML = "";
    return;
  }
  if (!state.selectedAgent) state.selectedAgent = sessions[0].key;
  $("#agent-list").innerHTML = sessions
    .map((session) => {
      const risk = String(session.risk_level || "low");
      const tokens = Number(session.total_tokens || 0);
      const cost = Number(session.cost_usd || 0);
      return `
        <div class="agent-card ${session.key === state.selectedAgent ? "active" : ""} ${Number(session.depth || 0) > 0 ? "child" : ""}" data-agent="${escapeHtml(session.key)}">
          <div class="agent-card-head">
            <strong>${Number(session.depth || 0) > 0 ? "↳ " : ""}${escapeHtml(session.name)}</strong>
            <span class="agent-risk ${risk}">${risk}</span>
            <em>${escapeHtml(session.model || "")}</em>
          </div>
          <div class="agent-card-meta">
            <span>tok <b>${fmt.qps(tokens)}</b></span>
            <span>$ <b>${cost.toFixed(2)}</b></span>
            <span>tools <b>${session.tool_calls ?? 0}</b></span>
            <span>fail <b>${session.failed_tool_calls ?? 0}</b></span>
            <span>loops <b>${session.retry_loops ?? 0}</b></span>
            ${session.child_sessions ? `<span>children <b>${session.child_sessions}</b></span>` : ""}
          </div>
        </div>`;
    })
    .join("");
  $$("#agent-list .agent-card").forEach((card) => {
    card.addEventListener("click", () => selectAgent(card.dataset.agent));
  });
  const selected = sessions.find((session) => session.key === state.selectedAgent) || sessions[0];
  $("#agent-detail-name").textContent = selected.name;
  const claim = selected.claim_status && selected.claim_status !== "unverifiable"
    ? `<div class="agent-claim ${escapeHtml(selected.claim_status)}">claim <b>${escapeHtml(selected.claim_root_cause || "—")}</b> · ${escapeHtml(selected.claim_status)}</div>`
    : "";
  const remediation = selected.remediation_status && selected.remediation_status !== "none"
    ? `<div class="agent-claim ${selected.remediation_status === "pending_validation" ? "mismatch" : "verified"}">remediation <b>${escapeHtml(selected.remediation_status)}</b> · ${Number(selected.risk_score || 0).toFixed(0)} risk</div>`
    : "";
  const delegation = selected.parent_session
    ? `<div class="agent-claim">delegated by <b>${escapeHtml(selected.parent_session)}</b> · role ${escapeHtml(selected.delegation_role || "subagent")}</div>`
    : selected.child_sessions
      ? `<div class="agent-claim verified">root session · ${selected.child_sessions} child(s) · subtree ${fmt.qps(selected.subtree_tokens)} tok · $${Number(selected.subtree_cost_usd || 0).toFixed(2)}</div>`
      : "";
  const tools = (selected.tool_timeline || [])
    .slice(-14)
    .map(
      (tool) => `<div class="agent-tool ${tool.status === "failed" ? "failed" : ""}">
        <span class="status"></span>
        <span class="tool">${escapeHtml(tool.tool)}</span>
        <span class="target">${escapeHtml(tool.target || "")}</span>
        ${tool.risk === "high" ? '<span class="risk">high</span>' : ""}
      </div>`
    )
    .join("");
  const reasons = (selected.reasons || []).map((reason) => `<span>${escapeHtml(reason)}</span>`).join("");
  $("#agent-detail").innerHTML = `
    ${claim}${remediation}${delegation}
    <div class="rank-reasons">${reasons}</div>
    <div class="agent-tools">${tools || '<p class="muted small">没有工具调用。</p>'}</div>`;
  $("#agent-findings").innerHTML = (agents.findings || [])
    .map(
      (finding) => `
      <div class="finding ${escapeHtml(finding.severity)}">
        <div class="finding-head"><span class="finding-tag">${escapeHtml(finding.severity)}</span><span class="finding-title">${escapeHtml(finding.title)}</span></div>
        <p>${escapeHtml(finding.detail)}</p>
      </div>`
    )
    .join("");
  renderBaseline(selected);
  renderGatePresets();
}

function renderBaseline(session) {
  const baseline = state.data.baseline || {};
  const row = (baseline.sessions || []).find((item) => item.key === session.key);
  $("#baseline-runs").textContent = `${baseline.history_runs || 0} runs`;
  if (!row) {
    $("#agent-baseline").innerHTML = '<p class="muted small">没有历史基线数据。</p>';
    $("#claim-accuracy").innerHTML = "";
    return;
  }
  const metrics = row.metrics || {};
  const labels = { total_tokens: "tokens", cost_usd: "cost", tool_calls: "tools", failed_tool_calls: "fail", retry_loops: "loops" };
  $("#agent-baseline").innerHTML = Object.entries(labels)
    .map(([key, label]) => {
      const metric = metrics[key] || {};
      const current = Number(metric.current || 0);
      const p95 = metric.p95 == null ? null : Number(metric.p95);
      const spike = p95 != null && current > Math.max(p95 * 1.25, p95 + 1);
      const value = key === "cost_usd" ? `$${current.toFixed(2)}` : key === "total_tokens" ? fmt.qps(current) : String(current);
      const base = p95 == null ? "no history" : `p95 ${key === "cost_usd" ? "$" + p95.toFixed(2) : key === "total_tokens" ? fmt.qps(p95) : p95}`;
      return `<div class="baseline-row ${spike ? "spike" : ""}"><span class="metric">${label}</span><span class="value">${value}</span><span class="delta">${spike ? "▲ " : ""}${base}</span></div>`;
    })
    .join("");
  const accuracy = baseline.claim_accuracy || {};
  const overall = accuracy.overall || {};
  $("#claim-accuracy").innerHTML = `
    <div><strong>${overall.accuracy == null ? "—" : `${Math.round(overall.accuracy * 100)}%`}</strong><span>claim accuracy</span></div>
    ${(accuracy.by_agent || []).map((item) => `<div><strong>${Math.round((item.accuracy || 0) * 100)}%</strong><span>${escapeHtml(item.agent)} · ${item.correct}/${item.total}</span></div>`).join("")}`;
}

function renderGatePresets() {
  const presets = state.data.policy || [];
  const container = $("#gate-presets");
  if (!presets.length || container.dataset.ready === "1") return;
  container.dataset.ready = "1";
  container.innerHTML = presets
    .map(
      (item, index) => `<button data-gate="${index}">${escapeHtml(item.label || item.tool)} → <b>${escapeHtml(item.decision)}</b></button>`
    )
    .join("");
  $$("#gate-presets button").forEach((button) => {
    button.addEventListener("click", () => {
      const item = presets[Number(button.dataset.gate)];
      runGate(item);
    });
  });
  runGate(presets[0]);
}

async function runGate(item) {
  const session = state.data.agents?.sessions?.find((entry) => entry.key === state.selectedAgent);
  const payload = {
    session: session?.key || item.session || "sre-claude-01",
    tool: item.tool,
    target: item.target,
    command: item.target,
  };
  const box = $("#gate-result");
  box.innerHTML = '<p class="muted small">evaluating policy…</p>';
  try {
    const response = await fetch("/api/gate", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(payload),
    });
    const decision = await response.json();
    if (!response.ok) throw new Error(decision.error || "gate failed");
    const cls = decision.decision === "deny" ? "deny" : decision.decision === "require_approval" ? "require_approval" : "allow";
    box.innerHTML = `
      <div class="gate-card ${cls}">
        <div class="gate-card-head">
          <span class="gate-decision">${escapeHtml(decision.decision)}</span>
          <em>${escapeHtml(decision.rule)}</em>
        </div>
        <p><code>${escapeHtml(decision.tool)}</code> ${escapeHtml(decision.target)}</p>
        <ul>${(decision.reasons || []).map((reason) => `<li>${escapeHtml(reason)}</li>`).join("")}</ul>
        ${decision.approvers?.length ? `<p>approvers: ${decision.approvers.map((item) => escapeHtml(item)).join(", ")}</p>` : ""}
      </div>`;
  } catch (error) {
    box.innerHTML = `<p class="muted small">gate error: ${escapeHtml(error.message)}</p>`;
  }
}

function selectAgent(key) {
  state.selectedAgent = key;
  const session = (state.data.agents?.sessions || []).find((item) => item.key === key);
  if (session) {
    const node = nodeByName(session.name);
    if (node) state.selected = node.id;
  }
  renderAgents();
  renderGraph();
}

function renderAlerts() {
  const data = state.data.alerts || {};
  const groups = data.groups || [];
  const stats = data.stats || {};
  const policies = data.policies || [];
  const brain = data.brain || {};
  const llm = brain.llm || {};
  $("#alert-group-count").textContent = `${groups.length} groups`;
  $("#alert-brain").textContent = `B R A I N · Laya ${llm.enabled ? "+ LLM" : "only (LLM off)"} · ${Object.entries(brain.actions || {}).map(([k, v]) => `${k}:${v}`).join(" · ")}`;
  $("#alert-summary").innerHTML = `
    <div><strong>${stats.input_events || 0}</strong><span>input events</span></div>
    <div><strong>${stats.groups || 0}</strong><span>groups</span></div>
    <div><strong>${Math.round((stats.compression_ratio || 0) * 1000) / 10}%</strong><span>compressed</span></div>
    <div><strong>${stats.silenced || 0}</strong><span>silenced</span></div>`;
  $("#alert-policies").innerHTML = policies
    .map(
      (policy) => `
      <div class="alert-policy">
        <b>${escapeHtml(policy.name || "")}</b>
        <span>${escapeHtml(policy.metric || "")} ${escapeHtml(policy.operator || "")} ${policy.threshold} · ${escapeHtml(policy.severity || "")}</span>
      </div>`
    )
    .join("");
  $("#alert-groups").innerHTML = groups
    .map(
      (group) => `
      <div class="alert-group ${state.selectedAlert === group.id ? "active" : ""}" data-alert="${escapeHtml(group.id || "")}">
        <div class="alert-group-head"><strong>${escapeHtml(group.title || "")}</strong><em>${escapeHtml(group.severity || "")}</em></div>
        <div class="alert-group-meta">
          <span><b>${group.member_count || 0}</b> members</span>
          <span><b>${group.inhibited_count || 0}</b> inhibited</span>
          <span>root ${escapeHtml((group.rca || {}).root_cause || "-")}</span>
          <span>${escapeHtml((group.decision || {}).source || "laya")} / ${escapeHtml((group.decision || {}).action || "-")} · FP ${Math.round(((group.decision || {}).false_alarm_probability || 0) * 100)}%</span>
        </div>
      </div>`
    )
    .join("");
  $$("#alert-groups .alert-group").forEach((card) => {
    card.addEventListener("click", () => selectAlert(card.dataset.alert));
  });
  if (!state.selectedAlert && groups.length) state.selectedAlert = groups[0].id;
  const selected = groups.find((group) => group.id === state.selectedAlert) || groups[0];
  $("#alert-detail-count").textContent = selected ? selected.id : "—";
  if (!selected) {
    $("#alert-detail").textContent = "暂无告警组";
    return;
  }
  const rca = selected.rca || {};
  const decision = selected.decision || {};
  const members = (selected.members || [])
    .slice(0, 12)
    .map(
      (member) => `
      <div class="alert-member">
        <span>${escapeHtml(member.target || "")} · ${escapeHtml(member.policy_name || "")}</span>
        <span>${escapeHtml(member.severity || "")}</span>
        <span>${member.inhibited_by ? "inhibited" : escapeHtml(member.status || "")}</span>
      </div>`
    )
    .join("");
  $("#alert-detail").innerHTML = `
    <p><b>自动 RCA：</b>${rca.auto ? "on" : "off"} · root <b>${escapeHtml(rca.root_cause || "-")}</b>
      ${rca.confidence ? `· ${Math.round(rca.confidence * 100)}%` : ""}</p>
    <p><b>Brain：</b>${escapeHtml(decision.source || "-")} / ${escapeHtml(decision.action || "-")} ·
      root prob ${Math.round((decision.root_probability || 0) * 100)}% ·
      误判风险 ${Math.round((decision.false_alarm_probability || 0) * 100)}% ·
      LLM ${escapeHtml((decision.llm || {}).status || "disabled")}</p>
    <p>${(rca.reasons || []).map(escapeHtml).join(" · ") || escapeHtml(rca.reason || "")}</p>
    <div>${members}</div>
    ${data.silenced_events?.length ? `<p><b>静默：</b>${data.silenced_events.length} 条（维护窗口）</p>` : ""}`;
}

function selectAlert(id) {
  state.selectedAlert = id;
  renderAlerts();
}

function renderCodeRca() {
  const data = state.data.code_rca || {};
  const iterations = data.iterations || [];
  const summary = data.summary || {};
  $("#code-iterations").textContent = `${iterations.length} iterations`;
  if (!iterations.length) {
    $("#code-summary").innerHTML = "";
    $("#code-headline").textContent = "运行 `python -m opsgraph code-demo` 生成代码级 RCA/REA 数据。";
    $("#code-list").innerHTML = "";
    $("#code-detail").innerHTML = '<p class="muted small">暂无代码级数据。</p>';
    return;
  }
  $("#code-summary").innerHTML = `
    <div><strong>${summary.fixed ?? 0}</strong><span>fixed</span></div>
    <div><strong>${Math.round((summary.success_rate || 0) * 100)}%</strong><span>success</span></div>
    <div><strong>${summary.rca_matches ?? 0}/${summary.iterations ?? 0}</strong><span>rca match</span></div>
    <div><strong>${summary.final_suite?.passed ? "PASS" : "FAIL"}</strong><span>regression</span></div>`;
  $("#code-headline").textContent = summary.headline || "";
  const codeGraph = state.data.code_graph?.summary || {};
  const realPerf = state.data.real_perf || {};
  const realPerfText = realPerf.fixed
    ? ` · real perf ${realPerf.fixed}/${realPerf.cases}`
    : "";
  $("#code-graph-line").textContent = codeGraph.entities
    ? `UModel code graph · ${codeGraph.entities} entities / ${codeGraph.relations} relations · ${codeGraph.files} files / ${codeGraph.functions} functions · ${summary.candidate_attempts || 0} candidates (${summary.rejected_candidates || 0} rejected)${realPerfText}`
    : "";
  if (!state.selectedCode) state.selectedCode = iterations[0].case_id;
  $("#code-list").innerHTML = iterations
    .map((item) => {
      const finding = item.rca?.finding || {};
      return `
        <div class="code-card ${item.case_id === state.selectedCode ? "active" : ""}" data-code="${escapeHtml(item.case_id)}">
          <div class="code-card-head">
            <strong>${item.iteration}. ${escapeHtml(item.title)}</strong>
            <span class="code-card-status ${escapeHtml(item.status)}">${escapeHtml(item.status)}</span>
          </div>
          <div class="code-card-meta">
            <span>${escapeHtml(finding.file || "")}:${finding.line || ""}</span>
            <span><b>${escapeHtml(finding.function || "")}</b></span>
            <span>${item.verification?.p99_before_ms} → ${item.verification?.p99_after_ms}ms</span>
          </div>
        </div>`;
    })
    .join("");
  $$("#code-list .code-card").forEach((card) => {
    card.addEventListener("click", () => selectCode(card.dataset.code));
  });
  const selected = iterations.find((item) => item.case_id === state.selectedCode) || iterations[0];
  renderCodeDetail(selected);
}

function selectCode(caseId) {
  state.selectedCode = caseId;
  renderCodeRca();
}

function renderCodeDetail(item) {
  const finding = item.rca?.finding || {};
  const verification = item.verification || {};
  const test = item.tests?.case || {};
  $("#code-detail-name").textContent = item.case_id;
  const before = (item.pseudocode?.before || []).map((line) => escapeHtml(line)).join("\n");
  const after = (item.pseudocode?.after || []).map((line) => escapeHtml(line)).join("\n");
  const reasons = (finding.reasons || []).map((reason) => `<li>${escapeHtml(reason)}</li>`).join("");
  const evidence = (finding.evidence || []).slice(0, 4).map((id) => escapeHtml(id)).join(", ");
  const candidates = (item.candidates || [])
    .map((candidate) => `${candidate.candidate_id}:${candidate.status}`)
    .join(" · ");
  $("#code-detail").innerHTML = `
    <h4>${escapeHtml(item.title)}</h4>
    <p>${escapeHtml(item.symptom)}</p>
    <div class="code-loc">${escapeHtml(finding.file || "")}:${finding.line || ""} · ${escapeHtml(finding.function || "")} · ${Math.round((finding.confidence || 0) * 100)}% confidence</div>
    <div class="code-evidence">
      <b>eBPF 证据</b>
      <ul>${reasons}</ul>
      <div>samples: ${evidence || "—"}</div>
    </div>
    <div class="code-pseudo">
      <div><span>伪代码 · 修复前</span><code>${before}</code></div>
      <div><span>伪代码 · 修复后</span><code>${after}</code></div>
    </div>
    <div class="code-verify">
      <div><b>${test.passed ? "PASS" : "FAIL"}</b>case test</div>
      <div><b>${verification.p99_after_ms}ms</b>${verification.p99_before_ms}ms → SLO ${verification.slo_p99_ms}ms</div>
      <div><b>${Math.round((verification.error_after || 0) * 100)}%</b>error after</div>
    </div>
    ${candidates ? `<p><b>候选补丁：</b>${escapeHtml(candidates)}</p>` : ""}
    <p><b>小白解释：</b>${escapeHtml(item.novice?.what || "")} ${escapeHtml(item.novice?.why || "")}</p>
    <p><b>修复：</b>${escapeHtml(item.novice?.fix || "")} ${escapeHtml(item.novice?.verify || "")}</p>`;
}

function renderQueries() {  const queries = state.data.queries || [];
  $("#query-list").innerHTML = queries
    .map(
      (query, index) => `
      <div class="query-card">
        <div class="query-card-head">
          <strong>${escapeHtml(query.label)}</strong>
          <button class="btn" data-query="${index}">运行</button>
        </div>
        <code>${escapeHtml(query.spl)}</code>
      </div>`
    )
    .join("");
  $$("#query-list button").forEach((button) => {
    button.addEventListener("click", () => runQuery(queries[Number(button.dataset.query)].spl));
  });
}

async function runQuery(spl) {
  const result = $("#query-result");
  $("#query-meta").textContent = "running";
  result.textContent = "执行中…";
  try {
    const response = await fetch("/api/query", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ spl }),
    });
    const payload = await response.json();
    if (!response.ok) throw new Error(payload.error || "query failed");
    $("#query-meta").textContent = `${payload.count} rows`;
    result.textContent = JSON.stringify(payload.rows?.slice(0, 30) ?? [], null, 2);
  } catch (error) {
    $("#query-meta").textContent = "error";
    result.textContent = String(error);
  }
}

function nodeById(id) {
  return (state.data?.nodes || []).find((node) => node.id === id);
}

function nodeByName(name) {
  return (state.data?.nodes || []).find((node) => node.name === name);
}

function selectService(name) {
  const node = nodeByName(name);
  if (!node) return;
  state.selected = node.id;
  state.simulation = null;
  renderGraph();
  showDetail(node);
}

function showDetail(node) {
  const card = $("#detail-card");
  const analysis = state.data.analysis || {};
  const role = node.role || "";
  const metrics = node.metrics || {};
  const isAgent = node.type === "ai.agent_session";
  card.classList.remove("hidden");
  const metricsHtml = isAgent
    ? `<div><strong>${fmt.qps(metrics.tokens)}</strong><span>tokens</span></div>
       <div><strong>$${Number(metrics.cost_usd || 0).toFixed(2)}</strong><span>cost</span></div>
       <div><strong>${Number(metrics.risk_score || 0).toFixed(0)}</strong><span>risk</span></div>
       <div><strong>${metrics.tool_calls ?? 0}</strong><span>tools</span></div>
       <div><strong>${metrics.failed_tool_calls ?? 0}</strong><span>failed</span></div>
       <div><strong>${node.status || "—"}</strong><span>risk level</span></div>`
    : `<div><strong>${fmt.qps(metrics.qps)}</strong><span>qps</span></div>
       <div><strong>${fmt.ms(metrics.p99_ms)}</strong><span>p99</span></div>
       <div><strong>${fmt.pct(Number(metrics.error_rate || 0) * 100)}</strong><span>errors</span></div>
       <div><strong>${Number(metrics.delta_pct || 0).toFixed(0)}%</strong><span>vs baseline</span></div>
       <div><strong>${Number(metrics.outbound_amp || 1).toFixed(1)}x</strong><span>outbound</span></div>
       <div><strong>${fmt.clock(metrics.onset_ts)}</strong><span>onset</span></div>`;
  card.innerHTML = `
    <button class="detail-close" aria-label="关闭">×</button>
    <h3>${escapeHtml(node.name)}</h3>
    <div class="detail-type">${escapeHtml(node.type)}${role ? " · " + escapeHtml(role) : ""}</div>
    <div class="detail-grid">${metricsHtml}</div>
    <div class="detail-actions">
      ${isAgent ? '<button class="btn" id="detail-agent">Agent 详情</button>' : '<button class="btn" id="detail-simulate">模拟摘除</button>'}
      <button class="btn" id="detail-query">UModel 查询</button>
      <button class="btn ghost" id="detail-focus">定位</button>
    </div>`;
  card.querySelector(".detail-close").addEventListener("click", () => {
    card.classList.add("hidden");
    state.selected = null;
    renderGraph();
  });
  const simulate = card.querySelector("#detail-simulate");
  if (simulate) simulate.addEventListener("click", () => simulateRemoval(node));
  const agentButton = card.querySelector("#detail-agent");
  if (agentButton) {
    agentButton.addEventListener("click", () => {
      const session = (state.data.agents?.sessions || []).find((item) => item.name === node.name);
      if (session) {
        state.selectedAgent = session.key;
        switchTab("agents");
        renderAgents();
      }
    });
  }
  card.querySelector("#detail-query").addEventListener("click", () => {
    switchTab("queries");
    const domain = node.type.split(".")[0];
    const spl = `.entity with(domain='${domain}', name='${node.type}', query='${node.name}') | limit 5`;
    runQuery(spl);
  });
  card.querySelector("#detail-focus").addEventListener("click", () => focusNode(node));
}

async function simulateRemoval(node) {
  if (node.type !== "svc.service") {
    showToast("只有服务节点支持摘除模拟");
    return;
  }
  try {
    const response = await fetch("/api/simulate", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ remove: [node.name] }),
    });
    const payload = await response.json();
    if (!response.ok) throw new Error(payload.error || "simulate failed");
    state.simulation = new Set([node.name, ...(payload.impacted || []).map((item) => item.service)]);
    renderGraph();
    showToast(`摘除 ${node.name}：${payload.count} 个下游服务受影响`);
  } catch (error) {
    showToast(String(error));
  }
}

function focusNode(node) {
  state.transform.k = 1.5;
  state.transform.x = 500 - node.x * 1.5;
  state.transform.y = 400 - node.y * 1.5;
  applyTransform();
}

function focusPath() {
  const analysis = state.data.analysis || {};
  const nodes = (analysis.path || []).map(nodeByName).filter(Boolean);
  if (nodes.length < 2) return;
  const xs = nodes.map((node) => node.x);
  const ys = nodes.map((node) => node.y);
  const minX = Math.min(...xs);
  const maxX = Math.max(...xs);
  const minY = Math.min(...ys);
  const maxY = Math.max(...ys);
  const width = Math.max(220, maxX - minX);
  const height = Math.max(180, maxY - minY);
  const k = Math.min(1.65, Math.max(1.0, Math.min(760 / width, 600 / height)));
  const cx = (minX + maxX) / 2;
  const cy = (minY + maxY) / 2;
  state.transform.k = k;
  state.transform.x = 500 - cx * k;
  state.transform.y = 400 - cy * k;
  applyTransform();
  showToast("已聚焦根因因果路径");
}

/* ------------------------------------------------------------------- graph */
function edgePath(source, target) {
  const dx = Math.max(46, Math.abs(target.x - source.x) * 0.42);
  return `M ${source.x} ${source.y} C ${source.x + dx} ${source.y}, ${target.x - dx} ${target.y}, ${target.x} ${target.y}`;
}

function visibleNode(node) {
  if (node.type === "biz.journey") return state.filters.business;
  if (node.type === "ai.agent_session") return state.filters.agents;
  if (node.type?.startsWith("ops.")) return false;
  return true;
}

function visibleEdge(edge) {
  if (edge.type === "calls" && !state.filters.calls) return false;
  if (edge.type === "depends_on" && !state.filters.business) return false;
  if (["acts_on", "investigates", "delegates_to"].includes(edge.type) && !state.filters.agents) return false;
  if (["targets", "affects"].includes(edge.type) && !state.filters.ops) return false;
  if (state.filters.observedOnly && edge.observed === false) return false;
  return true;
}

function renderGraph() {
  const svg = $("#graph");
  svg.innerHTML = "";
  const defs = document.createElementNS(NS, "defs");
  defs.innerHTML = `
    <marker id="arrow" viewBox="0 0 10 10" refX="9" refY="5" markerWidth="5" markerHeight="5" orient="auto-start-reverse">
      <path d="M 0 0 L 10 5 L 0 10 z" fill="rgba(120,151,190,.55)"></path>
    </marker>
    <marker id="arrow-hot" viewBox="0 0 10 10" refX="9" refY="5" markerWidth="5" markerHeight="5" orient="auto-start-reverse">
      <path d="M 0 0 L 10 5 L 0 10 z" fill="rgba(255,112,92,.9)"></path>
    </marker>
    <marker id="arrow-gold" viewBox="0 0 10 10" refX="9" refY="5" markerWidth="5" markerHeight="5" orient="auto-start-reverse">
      <path d="M 0 0 L 10 5 L 0 10 z" fill="rgba(251,191,36,.95)"></path>
    </marker>
    <marker id="arrow-violet" viewBox="0 0 10 10" refX="9" refY="5" markerWidth="5" markerHeight="5" orient="auto-start-reverse">
      <path d="M 0 0 L 10 5 L 0 10 z" fill="rgba(167,139,250,.9)"></path>
    </marker>`;
  svg.appendChild(defs);

  const viewport = document.createElementNS(NS, "g");
  viewport.setAttribute("id", "viewport");
  svg.appendChild(viewport);

  const nodes = state.data.nodes || [];
  const edges = state.data.edges || [];
  const byId = new Map(nodes.map((node) => [node.id, node]));
  const visibleIds = new Set(nodes.filter(visibleNode).map((node) => node.id));
  const analysis = state.data.analysis || {};
  const pathNames = analysis.path || [];
  const pathIds = pathNames.map((name) => nodeByName(name)?.id).filter(Boolean);
  const pathPairs = new Set();
  for (let i = 0; i < pathIds.length - 1; i += 1) pathPairs.add(`${pathIds[i]}->${pathIds[i + 1]}`);

  const edgeLayer = document.createElementNS(NS, "g");
  const pathLayer = document.createElementNS(NS, "g");
  const nodeLayer = document.createElementNS(NS, "g");
  viewport.appendChild(edgeLayer);
  viewport.appendChild(pathLayer);
  viewport.appendChild(nodeLayer);

  const tierLayer = document.createElementNS(NS, "g");
  [
    [48, "BUSINESS"],
    [185, "EDGE"],
    [345, "CORE"],
    [480, "PLATFORM"],
    [600, "PAYMENT"],
    [790, "CHANNEL"],
    [920, "DATA"],
  ].forEach(([x, label]) => {
    const text = document.createElementNS(NS, "text");
    text.setAttribute("class", "tier-label");
    text.setAttribute("x", String(x));
    text.setAttribute("y", "36");
    text.textContent = label;
    tierLayer.appendChild(text);
  });
  viewport.appendChild(tierLayer);

  const connected = new Set();
  if (state.selected) {
    edges.forEach((edge) => {
      if (edge.src === state.selected || edge.dst === state.selected) {
        connected.add(edge.src);
        connected.add(edge.dst);
      }
    });
  }

  const term = state.search.trim().toLowerCase();
  const matches = (node) => !term || String(node.name).toLowerCase().includes(term) || String(node.type).toLowerCase().includes(term);

  edges.forEach((edge) => {
    if (!visibleEdge(edge)) return;
    if (!visibleIds.has(edge.src) || !visibleIds.has(edge.dst)) return;
    const source = byId.get(edge.src);
    const target = byId.get(edge.dst);
    if (!source || !target) return;
    const path = edgePath(source, target);
    const g = document.createElementNS(NS, "g");
    const isPath = pathPairs.has(`${edge.src}->${edge.dst}`) && edge.type === "calls";
    const hot = edge.type === "calls" && ((Number(edge.p99_ms || 0) >= 800 && Number(edge.delta_pct || 0) >= 100) || Number(edge.error_rate || 0) >= 0.03);
    const classes = ["edge", edge.type === "calls" ? "call" : edge.type === "depends_on" ? "business" : ["acts_on", "delegates_to"].includes(edge.type) ? "agent" : "ops"];
    if (hot) classes.push("hot");
    if (!edge.observed) classes.push("unobserved");
    if (edge.shadow) classes.push("shadow");
    if (isPath) classes.push("root-path");
    if (state.selected && !connected.has(edge.src) && !connected.has(edge.dst)) classes.push("dim");
    if (term && !matches(source) && !matches(target)) classes.push("dim");
    if (state.simulation && state.simulation.has(source.name)) classes.push("dim");
    g.setAttribute("class", classes.join(" "));
    const line = document.createElementNS(NS, "path");
    line.setAttribute("d", path);
    line.setAttribute("class", classes.join(" "));
    const width = edge.type === "calls" ? 1.0 + Math.min(5.2, Math.log10(Number(edge.rps || 1) + 1) * 1.15) : 0.9;
    line.setAttribute("stroke-width", String(isPath ? Math.max(2.2, width) : width));
    line.setAttribute("marker-end", isPath ? "url(#arrow-gold)" : edge.shadow ? "url(#arrow-violet)" : hot ? "url(#arrow-hot)" : "url(#arrow)");
    const title = document.createElementNS(NS, "title");
    title.textContent = `${source.name} → ${target.name}\n${edge.type} · ${fmt.qps(edge.rps)} rps · P99 ${fmt.ms(edge.p99_ms)} · err ${fmt.pct(Number(edge.error_rate || 0) * 100)}${edge.shadow ? "\nshadow dependency (not declared)" : edge.observed === false ? "\ndeclared but unobserved" : ""}`;
    line.appendChild(title);
    g.appendChild(line);
    if (isPath) {
      const label = document.createElementNS(NS, "text");
      label.setAttribute("class", "edge-label");
      label.setAttribute("x", String((source.x + target.x) / 2));
      label.setAttribute("y", String((source.y + target.y) / 2 - 6));
      label.textContent = `${fmt.qps(edge.rps)} · ${fmt.ms(edge.p99_ms)}`;
      g.appendChild(label);
    }
    (isPath ? pathLayer : edgeLayer).appendChild(g);
  });

  nodes.forEach((node) => {
    if (!visibleNode(node)) return;
    const g = document.createElementNS(NS, "g");
    const classes = ["node"];
    if (node.name === analysis.root_cause) classes.push("root");
    if (node.name === analysis.victim) classes.push("victim");
    if (state.selected === node.id) classes.push("selected");
    if (state.selected && !connected.has(node.id) && state.selected !== node.id) classes.push("dim");
    if (term && !matches(node)) classes.push("dim");
    if (state.simulation && state.simulation.has(node.name)) classes.push("sim");
    g.setAttribute("class", classes.join(" "));
    g.setAttribute("transform", `translate(${node.x},${node.y})`);
    g.dataset.id = node.id;

    const halo = document.createElementNS(NS, "circle");
    halo.setAttribute("class", "halo");
    halo.setAttribute("r", String(Number(node.radius) + 9));
    halo.setAttribute("stroke", nodeStroke(node));
    g.appendChild(halo);

    const body = document.createElementNS(NS, "circle");
    body.setAttribute("class", "body");
    body.setAttribute("r", String(node.radius));
    body.setAttribute("fill", nodeFill(node));
    body.setAttribute("stroke", nodeStroke(node));
    if (node.type === "svc.external_service") body.setAttribute("stroke-dasharray", node.status === "shadow" ? "3 3" : "1 0");
    g.appendChild(body);

    const glyph = document.createElementNS(NS, "text");
    glyph.setAttribute("class", "glyph");
    glyph.setAttribute("fill", node.type === "svc.external_service" ? "#e9e3ff" : "#071018");
    glyph.textContent = node.type === "ai.agent_session" ? "AI" : initials(node.name);
    g.appendChild(glyph);

    if (node.type === "ai.agent_session" && node.status === "high") {
      const badge = document.createElementNS(NS, "text");
      badge.setAttribute("class", "badge");
      badge.setAttribute("y", String(-Number(node.radius) - 13));
      badge.setAttribute("fill", "#d9c9ff");
      badge.textContent = "HIGH RISK";
      g.appendChild(badge);
    }

    if (node.name === analysis.root_cause || node.name === analysis.victim) {
      const badge = document.createElementNS(NS, "text");
      badge.setAttribute("class", "badge");
      badge.setAttribute("y", String(-Number(node.radius) - 13));
      badge.setAttribute("fill", node.name === analysis.root_cause ? "#fbbf24" : "#ff8e9a");
      badge.textContent = node.name === analysis.root_cause ? "ROOT CAUSE" : "VICTIM";
      g.appendChild(badge);
    }

    const label = document.createElementNS(NS, "text");
    label.setAttribute("class", "label");
    label.setAttribute("y", String(Number(node.radius) + 15));
    label.textContent = node.label || node.name;
    g.appendChild(label);

    const sub = document.createElementNS(NS, "text");
    sub.setAttribute("class", "sub");
    sub.setAttribute("y", String(Number(node.radius) + 26));
    if (node.type === "svc.service" || node.type === "svc.external_service") {
      sub.textContent = `${fmt.qps(node.metrics?.qps)} qps · ${fmt.ms(node.metrics?.p99_ms)}`;
    } else if (node.type === "biz.journey") {
      sub.textContent = node.status === "impacted" ? "impacted" : "healthy";
    } else if (node.type === "ai.agent_session") {
      sub.textContent = `${fmt.qps(node.metrics?.tokens)} tok · $${Number(node.metrics?.cost_usd || 0).toFixed(2)}`;
    } else {      sub.textContent = node.status || "";
    }
    g.appendChild(sub);

    g.addEventListener("click", (event) => {
      event.stopPropagation();
      state.selected = state.selected === node.id ? null : node.id;
      if (state.selected) showDetail(node);
      else $("#detail-card").classList.add("hidden");
      renderGraph();
    });
    nodeLayer.appendChild(g);
  });

  applyTransform();
  $("#empty-state").classList.toggle("hidden", nodes.length > 0);
}

function applyTransform() {
  const viewport = $("#viewport");
  if (!viewport) return;
  const t = state.transform;
  viewport.setAttribute("transform", `translate(${t.x},${t.y}) scale(${t.k})`);
}

function svgPoint(event) {
  const svg = $("#graph");
  const point = svg.createSVGPoint();
  point.x = event.clientX;
  point.y = event.clientY;
  return point.matrixTransform(svg.getScreenCTM().inverse());
}

function zoomAt(x, y, factor) {
  const t = state.transform;
  const next = Math.min(3.2, Math.max(0.55, t.k * factor));
  const ratio = next / t.k;
  t.x = x - (x - t.x) * ratio;
  t.y = y - (y - t.y) * ratio;
  t.k = next;
  applyTransform();
}

function zoomBy(factor) {
  zoomAt(500, 400, factor);
}

function switchTab(name) {
  state.tab = name;
  $$(".tab").forEach((tab) => tab.classList.toggle("active", tab.dataset.tab === name));
  $$(".tab-panel").forEach((panel) => panel.classList.toggle("active", panel.id === `panel-${name}`));
}

/* --------------------------------------------------------------- listeners */
function bindEvents() {
  $$(".tab").forEach((tab) => tab.addEventListener("click", () => switchTab(tab.dataset.tab)));
  $$("[data-goto]").forEach((button) => {
    button.addEventListener("click", () => {
      switchTab(button.dataset.goto);
      if (button.dataset.goto === "agents") renderAgents();
    });
  });
  $$(".layer input[data-layer]").forEach((input) => {
    input.addEventListener("change", () => {
      state.filters[input.dataset.layer] = input.checked;
      renderGraph();
    });
  });
  $("#toggle-observed").addEventListener("change", (event) => {
    state.filters.observedOnly = event.target.checked;
    renderGraph();
  });
  $("#search").addEventListener("input", (event) => {
    state.search = event.target.value;
    renderGraph();
  });
  $("#btn-rerun").addEventListener("click", async () => {
    const button = $("#btn-rerun");
    button.disabled = true;
    button.innerHTML = "重算中…";
    try {
      const response = await fetch("/api/analyze", { method: "POST" });
      const payload = await response.json();
      if (!response.ok) throw new Error(payload.error || "analyze failed");
      state.data = payload;
      renderAll();
      showToast("RCA 已重算，排名与角色已回写 UModel");
    } catch (error) {
      showToast(String(error));
    } finally {
      button.disabled = false;
      button.innerHTML = '<span class="btn-dot"></span>重跑 RCA';
    }
  });
  $("#btn-brief").addEventListener("click", () => window.open("/api/brief", "_blank"));
  $("#btn-report").addEventListener("click", () => window.open("/api/report", "_blank"));
  $("#btn-focus").addEventListener("click", focusPath);
  $("#zoom-in").addEventListener("click", () => zoomBy(1.18));
  $("#zoom-out").addEventListener("click", () => zoomBy(1 / 1.18));
  $("#zoom-fit").addEventListener("click", () => {
    state.transform = { x: 0, y: 0, k: 1 };
    applyTransform();
  });

  const svg = $("#graph");
  let dragging = false;
  let dragStart = null;
  let dragOrigin = null;
  svg.addEventListener("mousedown", (event) => {
    if (event.target.closest(".node")) return;
    dragging = true;
    dragStart = svgPoint(event);
    dragOrigin = { x: state.transform.x, y: state.transform.y };
    svg.classList.add("dragging");
  });
  window.addEventListener("mousemove", (event) => {
    if (!dragging) return;
    const point = svgPoint(event);
    state.transform.x = dragOrigin.x + (point.x - dragStart.x);
    state.transform.y = dragOrigin.y + (point.y - dragStart.y);
    applyTransform();
  });
  window.addEventListener("mouseup", () => {
    dragging = false;
    svg.classList.remove("dragging");
  });
  svg.addEventListener("wheel", (event) => {
    event.preventDefault();
    const point = svgPoint(event);
    zoomAt(point.x, point.y, event.deltaY < 0 ? 1.1 : 1 / 1.1);
  }, { passive: false });
  svg.addEventListener("click", () => {
    if (!state.selected) return;
    state.selected = null;
    $("#detail-card").classList.add("hidden");
    renderGraph();
  });
  document.addEventListener("keydown", (event) => {
    if (event.key === "Escape") {
      state.selected = null;
      $("#detail-card").classList.add("hidden");
      renderGraph();
    }
  });
  window.addEventListener("resize", () => renderGraph());
}

bindEvents();
loadState();
