---
name: opsgraph-rca
description: >-
  Model-guided root-cause analysis over the OpsGraph UModel workspace. Use when
  an on-call engineer asks why a service is degraded, which upstream change
  caused it, what the blast radius is, or what to roll back. Triggers: "P99
  告警", "SLO 故障", "帮我排查", "root cause", "blast radius".
---

# OpsGraph RCA

Investigate an incident through the operational graph, not through a dashboard.

## Method

1. **Locate the victim.** Query `svc.service` by the alert name; read `status`,
   `latency_p99_ms`, `baseline_p99_ms`, `p99_delta_pct`, `role`.
2. **Walk upstream.** Traverse `calls` edges in reverse; collect every service
   within 2 hops and the edge evidence (`rps`, `error_rate`, `p99_delta_pct`,
   `onset_ts`, `declared`, `shadow`).
3. **Correlate changes.** Query `ops.change` for upstream services; compare
   `applied_ts` with the candidate's `amplification_onset_ts`.
4. **Check graph position.** A root cause is usually upstream of the victim and
   has a high-risk change close to the onset. A downstream service with no
   change is a victim, not a cause.
5. **Read the computed ranking.** Run `python -m opsgraph rca` and compare your
   manual ordering with it. Explain any disagreement.
6. **Quantify impact.** Read `biz.journey` entities with `impacted=true`; include
   conversion impact and revenue exposure.
7. **Report.** Root cause, evidence chain, blast radius, ruled-out hypotheses,
   one rollback action with risk + confirmation requirement.

## Commands

```bash
python -m opsgraph rca
python -m opsgraph query ".entity with(domain='svc', name='svc.service', query='payment-gateway')"
python -m opsgraph query ".entity with(domain='ops', name='ops.change', query='checkout')"
python -m opsgraph query ".entity with(domain='biz', name='biz.journey', query='impacted')"
```

## Guardrails

- Every causal statement must cite an edge metric or a change record.
- Do not ignore `shadow=true` or `observed=false` edges; report them as findings.
- Do not claim a node/CPU cause without checking that the victim has pods on it.
- Do not execute a rollback; propose it with risk and confirmation.
