window.__ModuleLoader__.load({
  id: "dsh-opsgraph-pet",
  factory: (require) => {
    var module = { exports: {} };
    var exports = module.exports;
    Object.defineProperty(exports, Symbol.toStringTag, { value: "Module" });

    const React = require("react");
    const h = React.createElement;
    const { useEffect, useState, useCallback } = React;

    const LOCAL_KEY = "opsgraph.localUrl";
    const CLOUD_KEY = "opsgraph.cloudUrl";
    const DEFAULT_URL = "http://127.0.0.1:18088";

    const CSS = `
.ops-pet-root{position:fixed;right:140px;bottom:22px;z-index:2147483001;font-family:ui-sans-serif,system-ui,-apple-system,"Segoe UI",sans-serif;color:#dbe7f5;pointer-events:none}
.ops-pet-root *{box-sizing:border-box}
.ops-pet-button{pointer-events:auto;position:relative;display:flex;align-items:flex-end;justify-content:center;width:92px;height:86px;padding:0;border:0;background:transparent;cursor:pointer;filter:drop-shadow(0 10px 18px rgba(0,0,0,.45))}
.ops-pet-orb{position:relative;width:64px;height:58px;border-radius:46% 46% 48% 48%;background:linear-gradient(160deg,#5eead4 0%,#2dd4bf 48%,#0f766e 100%);border:1px solid rgba(204,251,241,.6);box-shadow:inset -6px -8px 14px rgba(4,47,46,.55),inset 6px 6px 12px rgba(236,254,255,.28);animation:ops-pet-bob 3.2s ease-in-out infinite;transform-origin:50% 90%}
.ops-pet-ear{position:absolute;top:-10px;width:20px;height:22px;background:inherit;border:1px solid rgba(204,251,241,.5);border-radius:8px 8px 2px 2px;z-index:-1}
.ops-pet-ear.left{left:7px;transform:rotate(-18deg)}
.ops-pet-ear.right{right:7px;transform:rotate(18deg)}
.ops-pet-eye{position:absolute;top:22px;width:7px;height:9px;border-radius:50%;background:#062e2a;box-shadow:0 0 0 2px rgba(236,254,255,.25)}
.ops-pet-eye.left{left:17px}
.ops-pet-eye.right{right:17px}
.ops-pet-eye::after{content:"";position:absolute;left:2px;top:1px;width:2px;height:2px;border-radius:50%;background:#eafffb}
.ops-pet-mouth{position:absolute;left:50%;top:36px;width:12px;height:7px;margin-left:-6px;border-bottom:2px solid #062e2a;border-radius:0 0 8px 8px}
.ops-pet-cheek{position:absolute;top:32px;width:8px;height:4px;border-radius:50%;background:rgba(255,143,163,.55)}
.ops-pet-cheek.left{left:10px}.ops-pet-cheek.right{right:10px}
.ops-pet-bubble{position:absolute;right:82px;bottom:62px;min-width:120px;max-width:190px;padding:7px 9px;border:1px solid rgba(94,234,212,.3);border-radius:10px 10px 2px 10px;background:rgba(9,19,31,.92);color:#d8fff8;font-size:11px;line-height:1.4;box-shadow:0 8px 24px rgba(0,0,0,.32);white-space:normal}
.ops-pet-storm .ops-pet-orb{background:linear-gradient(160deg,#fda4af,#fb7185 55%,#9f1239);border-color:rgba(255,228,230,.8);animation:ops-pet-shake .55s ease-in-out infinite}
.ops-pet-storm .ops-pet-bubble{border-color:rgba(251,113,133,.5);color:#ffe4e6}
.ops-pet-happy .ops-pet-orb{background:linear-gradient(160deg,#a7f3d0,#34d399 55%,#047857);animation:ops-pet-hop 2.2s ease-in-out infinite}
.ops-pet-offline .ops-pet-orb{filter:grayscale(1);opacity:.62;animation:none}
.ops-pet-curious .ops-pet-orb{background:linear-gradient(160deg,#fde68a,#fbbf24 50%,#b45309)}
.ops-pet-panel{pointer-events:auto;position:absolute;right:0;bottom:104px;width:360px;max-height:min(600px,calc(100vh - 150px));overflow:auto;border:1px solid rgba(94,234,212,.22);border-radius:14px;background:linear-gradient(180deg,rgba(9,19,31,.98),rgba(6,13,24,.98));box-shadow:0 22px 55px rgba(0,0,0,.55);padding:13px}
.ops-pet-head{display:flex;align-items:center;justify-content:space-between;gap:8px;margin-bottom:8px}
.ops-pet-title{font-size:13px;font-weight:700;color:#f0fdfa;letter-spacing:.02em}
.ops-pet-sub{font-size:9px;color:#5eead4;font-family:ui-monospace,SFMono-Regular,Consolas,monospace;letter-spacing:.08em}
.ops-pet-close{pointer-events:auto;border:0;background:transparent;color:#94a3b8;font-size:16px;cursor:pointer;line-height:1;padding:2px 4px}
.ops-pet-row{display:flex;gap:6px;margin:6px 0}
.ops-pet-input{flex:1;min-width:0;border:1px solid rgba(148,163,184,.2);border-radius:6px;background:rgba(2,10,18,.75);color:#dbe7f5;font-family:ui-monospace,SFMono-Regular,Consolas,monospace;font-size:9px;padding:5px 6px;outline:none}
.ops-pet-input:focus{border-color:rgba(94,234,212,.55)}
.ops-pet-mini{pointer-events:auto;border:1px solid rgba(94,234,212,.25);border-radius:6px;background:rgba(45,212,191,.09);color:#99f6e4;font-size:9px;padding:5px 7px;cursor:pointer;white-space:nowrap}
.ops-pet-mini:hover{background:rgba(45,212,191,.18)}
.ops-pet-card{border:1px solid rgba(148,163,184,.16);border-radius:9px;background:rgba(15,23,42,.5);padding:8px;margin-top:7px}
.ops-pet-label{font-size:8px;color:#64748b;text-transform:uppercase;letter-spacing:.08em;font-family:ui-monospace,SFMono-Regular,Consolas,monospace}
.ops-pet-rootname{font-size:15px;font-weight:700;color:#f0fdfa;margin:3px 0}
.ops-pet-copy{font-size:10px;line-height:1.5;color:#94a3b8}
.ops-pet-grid{display:grid;grid-template-columns:repeat(3,1fr);gap:5px;margin-top:7px}
.ops-pet-stat{border:1px solid rgba(148,163,184,.13);border-radius:6px;padding:5px 6px;background:rgba(2,10,18,.45)}
.ops-pet-stat b{display:block;color:#e2e8f0;font-size:13px;font-family:ui-monospace,SFMono-Regular,Consolas,monospace}
.ops-pet-stat span{display:block;color:#64748b;font-size:7px;text-transform:uppercase;letter-spacing:.04em;margin-top:2px}
.ops-pet-actions{display:flex;flex-wrap:wrap;gap:5px;margin-top:8px}
.ops-pet-ack{margin-top:7px;padding:6px 7px;border-radius:6px;font-size:9px;line-height:1.45;background:rgba(45,212,191,.08);border:1px solid rgba(45,212,191,.18);color:#99f6e4;word-break:break-word}
.ops-pet-error{background:rgba(251,113,133,.08);border-color:rgba(251,113,133,.22);color:#fecdd3}
.ops-pet-note{margin-top:7px;color:#475569;font-size:8px;line-height:1.45}
@keyframes ops-pet-bob{0%,100%{transform:translateY(0) rotate(-1deg)}50%{transform:translateY(-6px) rotate(1deg)}}
@keyframes ops-pet-hop{0%,100%{transform:translateY(0) scale(1)}45%{transform:translateY(-9px) scale(1.02,.98)}55%{transform:translateY(-9px) scale(.98,1.02)}}
@keyframes ops-pet-shake{0%,100%{transform:translateX(0) rotate(-2deg)}25%{transform:translateX(-3px) rotate(2deg)}75%{transform:translateX(3px) rotate(-2deg)}}
@media (max-width:720px){.ops-pet-panel{width:min(340px,calc(100vw - 30px))}.ops-pet-root{right:10px;bottom:10px}}
`;

    if (
      typeof document !== "undefined" &&
      document.querySelector('style[data-plugin="dsh-opsgraph-pet"]') === null
    ) {
      const style = document.createElement("style");
      style.dataset.plugin = "dsh-opsgraph-pet";
      style.textContent = CSS;
      document.head.appendChild(style);
    }

    async function fetchState(url) {
      const controller = new AbortController();
      const timer = setTimeout(() => controller.abort(), 4500);
      try {
        const response = await fetch(`${url.replace(/\/$/, "")}/api/state`, {
          cache: "no-store",
          signal: controller.signal,
        });
        if (!response.ok) throw new Error(`HTTP ${response.status}`);
        return await response.json();
      } finally {
        clearTimeout(timer);
      }
    }

    function readUrl(key, fallback) {
      try {
        return window.localStorage.getItem(key) || fallback;
      } catch {
        return fallback;
      }
    }

    function PetOverlay() {
      const [open, setOpen] = useState(false);
      const [localUrl, setLocalUrl] = useState(() => readUrl(LOCAL_KEY, DEFAULT_URL));
      const [cloudUrl, setCloudUrl] = useState(() => readUrl(CLOUD_KEY, DEFAULT_URL));
      const [data, setData] = useState(null);
      const [error, setError] = useState("");
      const [ack, setAck] = useState(null);
      const [syncing, setSyncing] = useState(false);

      const load = useCallback(
        async (url = localUrl) => {
          try {
            const next = await fetchState(url);
            setData(next);
            setError("");
          } catch (exc) {
            setError(String(exc.message || exc));
          }
        },
        [localUrl]
      );

      useEffect(() => {
        load();
        const timer = setInterval(() => load(), 12000);
        return () => clearInterval(timer);
      }, [load]);

      const analysis = (data && data.analysis) || {};
      const brief = (data && data.brief) || {};
      const alerts = (data && data.alerts) || {};
      const partial = (data && data.partial) || {};
      const code = (data && data.code_rca) || {};
      const root =
        (partial.summary && partial.summary.root_service) ||
        analysis.root_cause ||
        (brief.partial && brief.partial.root_service) ||
        "-";
      const confidence =
        (partial.summary && partial.summary.confidence) ||
        analysis.confidence ||
        (brief.partial && brief.partial.confidence) ||
        0;
      const coverage =
        (partial.coverage && partial.coverage.service_pct) ||
        (brief.partial && brief.partial.coverage_pct) ||
        0;
      const recovered = Boolean(partial.summary && partial.summary.recovered);
      const storm = Boolean(alerts.storm && alerts.storm.detected);
      const alertGroups = (alerts.stats && alerts.stats.groups) || 0;
      const alertInput = (alerts.stats && alerts.stats.input_events) || 0;
      const codeFixes = (code.summary && code.summary.fixed) || 0;
      const p99 =
        (partial.summary && partial.summary.final_p99_ms) ||
        (brief.partial && brief.partial.final_p99_ms) ||
        0;

      const mood = error
        ? "offline"
        : storm
        ? "storm"
        : recovered
        ? "happy"
        : coverage && coverage < 70
        ? "curious"
        : "calm";
      const bubble = error
        ? "本地 OpsGraph 打盹中…点我重连"
        : storm
        ? `告警风暴！${alertInput} 条 → ${alertGroups} 组`
        : recovered
        ? `恢复啦 · 根因 ${root}`
        : coverage
        ? `${coverage}% 覆盖，盲区里找根因`
        : "盯着本地可观测性…";

      async function syncCloud() {
        setSyncing(true);
        setAck(null);
        try {
          const response = await fetch(`${cloudUrl.replace(/\/$/, "")}/api/coordinate`, {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({
              source: "dsh-opsgraph-pet",
              summary: {
                root,
                confidence,
                coverage_pct: coverage,
                recovered,
                storm,
                alert_input: alertInput,
                alert_groups: alertGroups,
                code_fixes: codeFixes,
                p99_ms: p99,
                captured_at: new Date().toISOString(),
              },
            }),
          });
          const payload = await response.json();
          if (!response.ok) throw new Error(payload.error || `HTTP ${response.status}`);
          setAck(payload);
        } catch (exc) {
          setAck({ error: String(exc.message || exc) });
        } finally {
          setSyncing(false);
        }
      }

      return h(
        "div",
        { className: `ops-pet-root ops-pet-${mood}` },
        open &&
          h(
            "section",
            {
              className: "ops-pet-panel",
              onClick: (event) => event.stopPropagation(),
            },
            h(
              "div",
              { className: "ops-pet-head" },
              h(
                "div",
                null,
                h("div", { className: "ops-pet-title" }, "OpsGraph · 端云协同"),
                h("div", { className: "ops-pet-sub" }, "LOCAL OBSERVABILITY")
              ),
              h(
                "button",
                {
                  className: "ops-pet-close",
                  onClick: () => setOpen(false),
                  "aria-label": "close",
                },
                "×"
              )
            ),
            h(
              "div",
              { className: "ops-pet-row" },
              h("input", {
                className: "ops-pet-input",
                value: localUrl,
                spellCheck: false,
                onChange: (event) => setLocalUrl(event.target.value),
                onBlur: () => {
                  try {
                    window.localStorage.setItem(LOCAL_KEY, localUrl);
                  } catch {}
                  load(localUrl);
                },
                title: "local OpsGraph URL",
              }),
              h(
                "button",
                { className: "ops-pet-mini", onClick: () => load(localUrl) },
                "刷新"
              )
            ),
            h(
              "div",
              { className: "ops-pet-row" },
              h("input", {
                className: "ops-pet-input",
                value: cloudUrl,
                spellCheck: false,
                onChange: (event) => setCloudUrl(event.target.value),
                onBlur: () => {
                  try {
                    window.localStorage.setItem(CLOUD_KEY, cloudUrl);
                  } catch {}
                },
                title: "cloud OpsGraph URL",
                placeholder: "cloud OpsGraph URL",
              }),
              h(
                "button",
                {
                  className: "ops-pet-mini",
                  disabled: syncing,
                  onClick: syncCloud,
                },
                syncing ? "同步中…" : "同步云端"
              )
            ),
            h(
              "div",
              { className: "ops-pet-card" },
              h("div", { className: "ops-pet-label" }, "INFERRED ROOT"),
              h("div", { className: "ops-pet-rootname" }, root),
              h(
                "div",
                { className: "ops-pet-copy" },
                error
                  ? `连接失败：${error}`
                  : `${confidence ? Math.round(confidence * 100) + "% 置信度" : "等待证据"} · ${
                      recovered ? "已恢复" : "推断中"
                    }${partial.summary && partial.summary.blind_spot ? " · 盲区根因" : ""}`
              )
            ),
            h(
              "div",
              { className: "ops-pet-grid" },
              stat("COVERAGE", `${coverage || 0}%`),
              stat("ALERT GROUPS", alertGroups || 0),
              stat("STORM", storm ? "YES" : "no"),
              stat("CODE FIXES", codeFixes || 0),
              stat("P99", `${p99 || 0}ms`),
              stat("ITERATIONS", (partial.summary && partial.summary.iterations) || 0)
            ),
            h(
              "div",
              { className: "ops-pet-actions" },
              h(
                "button",
                {
                  className: "ops-pet-mini",
                  onClick: () =>
                    window.open(localUrl.replace(/\/$/, "") + "/", "_blank", "noopener"),
                },
                "打开图控制台"
              ),
              h(
                "button",
                { className: "ops-pet-mini", onClick: () => setOpen(false) },
                "收起"
              )
            ),
            ack &&
              h(
                "div",
                { className: `ops-pet-ack ${ack.error ? "ops-pet-error" : ""}` },
                ack.error
                  ? `同步失败：${ack.error}`
                  : `云端已接收 ${ack.request_id} · 云端根因 ${ack.cloud_root || "-"} · ${
                      ack.cloud_confidence
                        ? Math.round(ack.cloud_confidence * 100) + "%"
                        : "-"
                    }`
              ),
            h(
              "div",
              { className: "ops-pet-note" },
              "只读本地 OpsGraph；同步只发送摘要，不发送日志、代码或凭据。"
            )
          ),
        h(
          "button",
          {
            className: "ops-pet-button",
            onClick: () => setOpen((value) => !value),
            "aria-label": "OpsGraph desktop pet",
            title: bubble,
          },
          h(
            "span",
            { className: "ops-pet-bubble" },
            bubble
          ),
          h(
            "span",
            { className: "ops-pet-orb" },
            h("span", { className: "ops-pet-ear left" }),
            h("span", { className: "ops-pet-ear right" }),
            h("span", { className: "ops-pet-eye left" }),
            h("span", { className: "ops-pet-eye right" }),
            h("span", { className: "ops-pet-cheek left" }),
            h("span", { className: "ops-pet-cheek right" }),
            h("span", { className: "ops-pet-mouth" })
          )
        )
      );
    }

    function stat(label, value) {
      return h(
        "div",
        { className: "ops-pet-stat" },
        h("b", null, String(value)),
        h("span", null, label)
      );
    }

    function apply(ctx) {
      ctx.slots.inject("shell.overlay", () =>
        ctx.slots.register(
          { name: "shell.overlay", id: "opsgraph-pet", order: 40 },
          PetOverlay
        )
      );
    }

    exports.apply = apply;
    exports.inject = ["slots"];
    return module.exports;
  },
});
