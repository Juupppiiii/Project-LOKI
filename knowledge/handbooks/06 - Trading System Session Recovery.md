# Recover a trading system session

keywords: trading session not responding, poll timing slow, SWERR, lane service running on both nodes, deal not picking up brokerage, restart trading server, trading app hung
group: IAS Trading Systems
params: hostname, service_name, user_id, deal_id, counterparty
notes: Auto-drafted from 293 similar resolutions (e.g. INC73531623, INC73435299, INC73435298, INC73432063, INC73418845). Advisory draft; human review required.

## Steps

1. check | Confirm the reported symptom and capture scope: affected hostname, service_name, user_id/session and deal_id. Note whether the issue is session/API latency, a duplicate service instance, or a trade/static-data configuration gap. | risk: R0
2. connect | Connect to the affected trading application host (hostname) and, where the service is clustered (e.g. LD5 and ARK), to both nodes. | risk: R0
3. check | Review the application verbose log for the session (UserId=user_id) — look for `Called API Function: Poll` timings and return codes (SWERR_Success vs errors) to confirm whether the session is slow, stalled, or healthy. | risk: R0
4. check | Verify service instance state on each node (service_name) to detect a duplicate/split-brain condition where the same service is running on both the primary and secondary node. | risk: R0
5. manual | If the deal/trade is failing due to configuration rather than service health, validate the executing entity and schedule against the static data (e.g. trade booked under CITIBANK NY but brokerage only configured for CITI LDN) and have the trade support owner add the missing brokerage/schedule entry, then re-trigger the deal to confirm it populates. | risk: R3
6. restart_service | If a duplicate instance was found, stop service_name on the non-owning node only (e.g. stop the LD5 instance leaving ARK active); otherwise restart service_name on the affected host to clear the hung sessions. | risk: R3
7. reboot | Only if the service restart does not restore polling, reboot the trading server (hostname) during an approved window with trading desk sign-off. | risk: R3
8. notify | Re-check session poll timings and service state post-change, then update the ticket with actions taken, close as Solved (Work Around), and link/close any related problem record and child tasks. | risk: R0
