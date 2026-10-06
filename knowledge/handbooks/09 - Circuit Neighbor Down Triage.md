# Triage an API neighbor / circuit down alert

keywords: neighbor down, api neighbor down, circuit down, neighbor down via syslog, bgp neighbor
group: Network Operations
params: hostname
notes: Drafted from 13 recurring closed incidents (e.g. INC73438686, INC73437565, INC73436136) in Network Operations. The close notes show the same shape every time -- "Circuit Down: <t1> ... Circuit Recovery: <t2> ... Circuit Downtime: <N> seconds ... Ticket RFO: Carrier Routing/Core Issue" -- i.e. a short carrier/circuit flap that RECOVERS ON ITS OWN within seconds, closed as "Third Party Fix". There is no remediation action to automate here: the fix is the carrier's. So this procedure is pure triage -- confirm the neighbor is back up, capture the downtime window and the carrier RFO, and record it. Every step is read-only or a note; nothing changes the network. If the neighbor is still down when checked, it is handed to a human to chase the carrier.

## Steps

1. check | Confirm which neighbor/circuit alerted on {hostname} and when | risk: R0
2. check | Check whether the neighbor/circuit has already recovered (state is back to FULL/up) | risk: R0
3. check | Capture the circuit-down and circuit-recovery timestamps and compute the downtime window | risk: R0
4. manual | If the circuit is still down, chase the carrier for a Reason For Outage (RFO); this is a human-owned third-party action
5. notify | Record the downtime window and carrier RFO on the ticket and close as Third Party Fix if recovered
