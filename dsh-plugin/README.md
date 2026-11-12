# dsh-opsgraph-pet

Local observability + end-cloud RCA bridge for DeepSeek Harness, plus a desktop pet.

## What it does

- **Desktop pet** in the DSH `shell.overlay`:
  - happy when OpsGraph is healthy/recovered;
  - anxious (shake/red) when an alert storm is detected;
  - curious (amber) when eBPF coverage is below 70% and the root is a blind spot;
  - asleep/grey when the local OpsGraph API is unreachable.
- Click the pet to open a compact **OpsGraph** panel:
  - inferred root, confidence, coverage, P1 alert groups, storm flag, code fixes, P99;
  - local OpsGraph URL and cloud OpsGraph URL;
  - open the full graph console;
  - **同步云端** sends only a summary to `POST /api/coordinate` and displays the cloud RCA acknowledgement.
- No host tools, no dependencies, no credentials. The host half exists only so the
  DSH loader sees `dsh.client`; the browser half talks to the local OpsGraph HTTP API.

## Install (linked local package)

```powershell
dsh plugin add E:\ebpf\opsgraph\dsh-plugin --profile web
```

The profile must also list the bundle (or use the plugin manager's bundle install):

```json
{
  "dependencies": {
    "dsh-opsgraph-pet": "link:E:/ebpf/opsgraph/dsh-plugin"
  },
  "dsh": {
    "profile": {
      "bundles": ["@deepseek-ai/dsh-base", "@deepseek-ai/dsh-web-app", "dsh-opsgraph-pet"]
    }
  }
}
```

Then reload the DSH web page. Start OpsGraph first:

```powershell
cd E:\ebpf\opsgraph
python -m opsgraph serve --port 18088
```

The pet appears bottom-right. The default local/cloud URL is `http://127.0.0.1:18088`;
the cloud URL can point at a remote OpsGraph instance.

## Local API contract

`GET /api/state` (CORS enabled) returns `analysis`, `alerts`, `partial`, `code_rca`,
and `brief`. `POST /api/coordinate` accepts:

```json
{
  "source": "dsh-opsgraph-pet",
  "summary": {"root": "...", "coverage_pct": 60, "storm": false}
}
```

and appends the summary to `artifacts/coordination.jsonl`, returning the cloud root,
confidence, coverage, and a request id.

## Boundary

- The plugin only sends the compact summary above; never logs, code, prompts, or credentials.
- It reads OpsGraph's replay/demo API by default; point `localUrl` at a real OpsGraph
  gateway for production.
- CORS is enabled for the local console; do not expose that console to an untrusted network.
