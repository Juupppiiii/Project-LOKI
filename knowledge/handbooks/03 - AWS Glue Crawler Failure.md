# Handle an AWS Glue crawler failure

keywords: glue crawler, issue encountered with glue crawlers, crawler failed, glue job alert, aws glue, false alert, replay crawler
group: CDP
params: crawler_name, aws_account, region, database_name, ticket_number
notes: Auto-drafted from 608 similar resolutions (e.g. INC73168725, INC73161744, INC73162111, INC73131481, INC73132859). Advisory draft; human review required.

## Steps

1. check | Read the alert work notes and extract the Glue crawler name, AWS account, and region referenced in the ticket | risk: R0
2. connect | Connect to the target AWS account/region with read access to AWS Glue and CloudWatch Logs | risk: R0
3. check | Review the crawler's last crawl status and run history (state, error message, start/end time) to confirm whether the failure is transient or persistent | risk: R0
4. check | Check the crawler's CloudWatch log stream and the target S3 path / Glue database for the underlying cause (throttling, transient access error, empty or locked partition) | risk: R0
5. run_command | Replay/re-run the Glue crawler for the affected database and wait for it to reach SUCCEEDED | risk: R2
6. check | Confirm the replayed crawl completed successfully and the Glue catalog tables/partitions are updated; verify no new alert has been raised | risk: R0
7. notify | If the replay succeeded with no data impact, close the ticket as False Alert with the replay timestamp and crawler name in the work notes; if the crawler fails again, escalate to the CDP data platform team with log excerpts | risk: R1
