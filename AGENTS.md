# AGENTS.md

OpsGraph is a Python reference implementation of eBPF-observed operational graph engineering on top of UModel.

## Project In One Minute

- `opsgraph/ontology.py` — the graph contract (12 EntitySets, 14 Links).
- `opsgraph/scenario.py` — deterministic DeepFlow-shaped replay with a retry-storm incident.
- `opsgraph/materialize.py` — observations + declared topology -> UModel entities/relations with evidence.
- `opsgraph/analysis.py` — explainable RCA, blast radius, drift, SPOF, timeline.
- `opsgraph/console_server.py` + `console/` — zero-dependency SVG graph console.
- `model/` — generated UModel model pack; `data/replay/` — generated replay; `artifacts/` — generated output.

## Commands

```bash
python -m opsgraph demo --addr http://127.0.0.1:18080
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
- Update `docs/en`-style paired docs only if the project gains bilingual docs; currently Chinese README plus English code comments is intentional.
- Regenerate `model/` after changing `ontology.py` (`python -m opsgraph model`).

## Verification Before Final Response

```bash
python -m unittest discover -s tests -v
python -m ruff check opsgraph tests
python -m opsgraph demo --addr http://127.0.0.1:18080
```

Report the root cause, confidence, entity/relation counts, and any skipped checks.
