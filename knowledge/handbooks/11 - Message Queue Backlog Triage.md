# Triage a message queue backlog alert

keywords: queue messages, messages backing up, message spool age, queue messages above, spooled, zero consumers
group: IAS Trading Systems
params: queue_name
notes: Drafted from ~50 recurring closed incidents (e.g. INC73627989, INC73438348, INC73438071) across IAS Trading Systems and Liquidnet NOC. The alerts are Solace/queue depth or spool-age thresholds ("messages backing up (>1000 spooled)", "spool age of 85 seconds", "messages above 3000", "zero consumers bound"). The closures are mixed and telling: many are "False Alert" (grafana_state_reason = Error) or acknowledgements ("this is ok, already raised"), and only some are genuine backlogs. So the real work is TRIAGE: decide whether this is a flapping/false alert or a real backlog, and whether consumers are bound. The agent can gather that evidence read-only; the remediation decision (purge, restart a consumer, re-point) is a real operational change and stays with a human -- it is deliberately NOT codified as an agent action in this draft.

## Steps

1. check | Confirm the alerting queue {queue_name} and the threshold that fired | risk: R0
2. check | Check the current queue depth / spool age and whether it is still rising or already clearing | risk: R0
3. check | Check whether consumers are bound to the queue (a backlog with zero consumers is the real signal) | risk: R0
4. check | Check whether the alert is a known false/flapping alert (e.g. a monitoring grafana error) rather than a true backlog | risk: R0
5. manual | Decide the remediation: no action (false alert / self-clearing), or restart/re-bind a consumer, or escalate to the app team -- human-owned operational decision
6. notify | Record the queue state, consumer status, and the decision on the ticket
