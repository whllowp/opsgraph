# AGENTS.md

OpsGraph is a Python reference implementation of eBPF-observed operational graph engineering on top of UModel.

## Project In One Minute

- `opsgraph/ontology.py` — the graph contract (22 EntitySets, 29 Links, incl. AI + code domains).
- `opsgraph/agentsight.py` — AgentSight snapshot/SQLite adapter.
- `opsgraph/agent_graph.py` — agent session/tool/effect graph + delegation tree + AI graph operators.
- `opsgraph/policy.py` — pre-execution allow/require_approval/deny gate.
- `opsgraph/history.py` — historical baseline + claim accuracy.
- `opsgraph/code_rca.py` — eBPF code-stack RCA + REA loop; `opsgraph/code_cases.py` — 10 fintech incidents.
- `opsgraph/patch_engine.py` — candidate patch safety, test rejection, rollback.
- `demo/fintech/` — intentionally buggy financial code + 10 regression tests.
- `opsgraph/scenario.py` — deterministic DeepFlow-shaped replay with a retry-storm incident.
- `opsgraph/materialize.py` — observations + declared topology -> UModel entities/relations with evidence.
- `opsgraph/analysis.py` — explainable RCA, blast radius, drift, SPOF, timeline.
- `opsgraph/console_server.py` + `console/` — zero-dependency SVG graph console.
- `model/` — generated UModel model pack; `data/replay/` — generated replay; `artifacts/` — generated output.

## Commands

```bash
python -m opsgraph demo --addr http://127.0.0.1:18080
python -m opsgraph gate --session sre-claude-01 --tool kubectl_apply --target "kubectl apply -f checkout-retry-policy.yaml -n shop"
python -m opsgraph baseline
python -m opsgraph code-demo
python -m opsgraph code-ingest --input perf-stacks.txt --case case-01
python -m opsgraph analyze --source umodel
python -m opsgraph serve --port 18088
python -m unittest discover -s tests -v
python -m ruff check opsgraph tests
node --check console/app.js
```

## Invariants

- Do not read graph data from the console bypassing UModel; `/api/query` must go through Query Service.
- Keep `calls` relations evidence-carrying; never add an edge without `observed`/`declared`/`evidence`.
- Do not drop shadow or declared-unobserved edges; they are product features.
- Keep entity IDs derived from semantic natural keys via `opsgraph/ids.py`.
- Do not let `analysis.py` read `scenario.expected_root_cause`.
- AgentSight `claim` is not truth: keep `claim_status` derived from graph RCA and `remediation_status` derived from eBPF service status.
- Do not commit raw AgentSight snapshots; they contain prompts, paths, and commands. Commit only derived `artifacts/agents.json`.
- Gate decisions are advisory: do not claim the gate enforces syscalls unless a tool proxy/admission layer is actually wired.
- Baseline history is synthetic in the demo; never present it as production telemetry.
- Code-RCA 10-iteration demo uses replay data; `data/code_rca/perf-sample.txt` is a real perf calibration, not a live end-to-end capture. Do not claim live kernel capture without a Linux perf/bpftrace run.
- REA applies patches only in a temp worktree; never auto-commit or auto-deploy a generated fix.
- Rejected candidate patches must stay visible in `candidates`; do not silently discard failed attempts.
- Update `docs/en`-style paired docs only if the project gains bilingual docs; currently Chinese README plus English code comments is intentional.
- Regenerate `model/` after changing `ontology.py` (`python -m opsgraph model`).

## Verification Before Final Response

```bash
python -m unittest discover -s tests -v
python -m ruff check opsgraph tests
python -m opsgraph demo --addr http://127.0.0.1:18080
python -m opsgraph code-demo
```

Report the root cause, confidence, entity/relation counts, and any skipped checks.
