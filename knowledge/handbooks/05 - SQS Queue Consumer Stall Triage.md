# Triage an SQS queue consumer stall

keywords: SQS messages are not being picked, SQS messages not being picked for last 120 secs, SQS consumer stalled, queue not consuming, SQS backlog alert
group: Cloud Support
params: queue_name, aws_account, region, consumer_service
notes: Auto-drafted from 412 similar resolutions (e.g. INC73390714, INC73354976, INC73355191, INC73301334, INC73309334). Advisory draft; human review required.

## Steps

1. check | Confirm the alert details from the work notes: queue_name, aws_account, region and the stall duration (e.g. "not picked for last 120 secs"). | risk: R0
2. check | Verify whether a CDP release / planned deployment or maintenance window is currently in progress for consumer_service; if so, the stall is expected and the ticket is annotated as "cdp release on going". | risk: R0
3. connect | Connect to the AWS account/region monitoring console or jump host with read-only access for queue_name. | risk: R0
4. run_command | Retrieve current queue metrics for queue_name (ApproximateNumberOfMessagesVisible, ApproximateAgeOfOldestMessage, NumberOfMessagesReceived) to confirm whether consumption has resumed. | risk: R0
5. check | Re-check the alert after the observation window: if message age is dropping and messages are being received again, record as self recovered. | risk: R0
6. notify | Post work notes with the outcome (self recovered / CDP release ongoing / still stalled) and tag the consumer_service application team if the stall persists. | risk: R1
7. manual | If consumption has not resumed and no release is in progress, hand off to the consumer_service owning team for consumer restart or scaling decision — do not restart or purge the queue without owner approval. | risk: R3
