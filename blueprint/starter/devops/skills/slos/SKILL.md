---
name: slos
description: Define service level indicators and objectives, compute the error budget and set burn-rate alerts that
  page only when users are affected. Use it when the user asks how reliable a service should be, wants SLOs, error
  budgets or quieter alerts, or sets up monitoring for a new service.
license: MIT
metadata:
  sources: agency-agents/engineering/engineering-sre.md
---

# SLOs

An SLO says how reliable a service must be for its users, as a number. It turns "is it good enough?" into a
measurement, and the error budget it leaves decides when reliability work comes before features.

## 1. Pick what users notice

An indicator (SLI) is the share of good events among all valid events, measured as close to the user as you can (the
load balancer or the client rather than the process itself).

| Kind of service | Indicator | Good event |
|---|---|---|
| Request and response (API, website) | Availability | A valid request that did not fail with a server error |
| Request and response | Latency | A request answered within a threshold (for example 300 ms) |
| Pipeline or batch job | Freshness | Data no older than the agreed age when read |
| Pipeline or batch job | Correctness | A run whose quality checks passed |
| Queue consumer | Processing time | A message handled within a threshold after it arrived |

Pick two or three per service. Leave out requests that are not the service's fault (a client error, a health
check).

## 2. Set the objective

- Start from what the service achieves now and what users need; never from 100%. Every extra nine costs a lot and
  users rarely notice it.
- Use a rolling window of 28 or 30 days.
- Use thresholds, not averages, for latency: "99% of requests within 300 ms" says something; the average hides the
  slow tail.

## 3. The error budget

The budget is what the objective allows to fail: 1 minus the target.

| Target | Budget over 30 days |
|---|---|
| 99% | 7 hours 12 minutes |
| 99.5% | 3 hours 36 minutes |
| 99.9% | 43 minutes |
| 99.95% | 22 minutes |

Agree with the user on a budget policy: while budget is left, ship as usual; when it is spent, reliability work comes
first until it recovers, and risky changes wait.

## 4. Alert on the burn rate

The burn rate is how fast the budget is being used: 1 means it lasts exactly the window. Alert on that, not on every
error, so a short blip does not page anyone and a real outage does.

| Severity | Burn rate | Long window | Short window | Budget used when it fires |
|---|---|---|---|---|
| Page | 14.4 | 1 hour | 5 minutes | 2% |
| Page | 6 | 6 hours | 30 minutes | 5% |
| Ticket | 1 | 3 days | 6 hours | 10% |

Both windows must be over the rate: the long one shows it matters, the short one that it is still going on, so the
alert stops soon after recovery. For a 99.9% target, a burn rate of 14.4 means an error ratio above 1.44% (14.4 x
0.1%).

As a query, the indicator is a ratio of two counters, for example for availability in Prometheus:

```
sum(rate(http_requests_total{job="orders-api", code!~"5.."}[1h]))
/
sum(rate(http_requests_total{job="orders-api"}[1h]))
```

Write recording rules for each window and alert on them; follow the monitoring system the project uses.

## 5. Use it

- Show the SLO and the remaining budget on one dashboard per service.
- Look at the budget in planning; an incident's postmortem says how much budget it used.
- Review the objective every few months: too strict when it pages for things users never noticed, too loose when
  users complain while it is green.

## In YAAP

- Write the SLO document and the alert rules as files in the repository, next to the service.
- Record the service's SLOs in the project memory with `update_memory`, so changes and reviews can refer to them.
- For a larger task, when `delegate` is available (in a project conversation), delegate to the `devops` agent
  (`agent: devops`) and review its diff.

## SLO document

```
Service: <name, owner>
Users: <who depends on it, and for what>
SLIs: <per indicator: good event, valid event, where it is measured>
Objectives: <per SLI: target and window>
Error budget: <per objective, in time or events>
Alerts: <burn rates and windows, page or ticket>
Budget policy: <what happens when the budget is spent>
Review: <when the objective is next reviewed>
```
