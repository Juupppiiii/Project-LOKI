# Handle an AWS Spot Recalled instance

keywords: spot recalled, spot instance recalled, tpicap-gtn-nonprod, instance recalled
group: FX Project Managers
params: instance_id, hostname
notes: Drafted from 15 recurring closed incidents (e.g. INC73438761, INC73438672, INC73437431) raised by "SNOW AWS User". Each alert is "Spot Recalled: <hostname> (<instance-id>)" with a work note "ID: i-... | Env: AMDEV | App: Front Office | Action: terminate". The recorded action is to terminate the reclaimed spot instance. Terminating an EC2 instance is destructive and irreversible, so that step is deliberately HUMAN-owned here; the agent only does the safe, bounded parts (confirm it really is a recalled non-prod spot, then record the outcome). Codifying the terminate as an automated action would require a dedicated, separately reviewed AWS connector and its own risk sign-off — it is intentionally not done in this draft.

## Steps

1. check | Confirm the alert is a Spot Recalled event for a non-prod GTN instance | risk: R0
2. check | Confirm the instance id {instance_id} matches the recalled host {hostname} and the environment is non-prod (AMDEV) | risk: R0
3. check | Confirm the instance is already being reclaimed by AWS and carries no protected workload | risk: R0
4. manual | Decide whether to terminate the recalled spot instance (destructive, irreversible) or let AWS reclaim it
5. reboot | Terminate the recalled spot instance {instance_id} (human-performed in the AWS console / CLI; not agent-executed)
6. notify | Record on the ticket that the recalled spot instance was handled, with the instance id and environment
