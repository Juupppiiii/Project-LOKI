# Verify SMTP is listening

keywords: smtp service on host, listen state, smtp not listening, postfix is not running
group: Global Linux Support
params: server
notes: |
  Validated against 96 real closed alerts of exactly this wording.

  Two things learned from that validation, both encoded above and below:

  1. The alert text contains a DOUBLE space - "is not in  LISTEN state" - so a
     keyword of "not in listen state" never matches it. The keyword is
     "listen state" instead. Without this, 72 of the 96 alerts were missed,
     every one of them from Liquidnet NOC Support: they scored 2.0 against a
     threshold of 3.0 because only one keyword hit and they get no
     assignment-group point. Two matching keywords now clear the threshold on
     their own, so the group no longer decides whether the ticket is seen.

  2. Postfix is sometimes switched off deliberately. Two tickets closed with
     "postfix has been disabled" and "postfix was disabled from these servers".
     Reporting "service inactive" on those hosts sends the engineer chasing a
     non-problem, so step 2 checks whether the unit is enabled before the
     result is interpreted.

  Ticket volume sits across Global Linux Support (206) and Liquidnet NOC Support
  (72), so the procedure must work for both.

## Steps

1. check | Is the mail service running right now | command: systemctl is-active postfix | expect: active | risk: R0
2. check | Is it supposed to be running, or was it disabled on purpose | command: systemctl is-enabled postfix | expect: enabled | risk: R0
3. check | Is anything actually listening on port 25 | command: ss -lntp | grep ':25' | risk: R0
4. check | Is the mail queue backing up | command: postqueue -p | tail -1 | risk: R0
5. notify | Post all four readings so the engineer can close or escalate | risk: R1
6. manual | If the service is down and should be up, start it and confirm recovery
7. manual | If it was disabled deliberately, suppress the alert for this host instead of restarting

# Review a Commvault backup failure report

keywords: backup job summary report, commvault, backup failure reports
group: ICS Infrastructure Support
params: server
notes: 42 closed tickets. 74% resolved as "No actions required" - the failures were expected (policy transitions, hosts being rerun as FULL). The judgement about whether a failure is benign stays with a person; the agent only gathers the evidence needed to make that call.

## Steps

1. check | List the failed jobs in the report window | command: qoperation execute -af GetJobSummary -failedOnly | risk: R0
2. check | Group the failures by client and failure reason | risk: R0
3. check | Flag any client whose last successful backup is older than its policy allows | risk: R0
4. notify | Post the grouped summary on the ticket | risk: R1
5. manual | Decide whether the failures are expected (policy change, planned rerun) or need escalation
6. manual | If a rerun is needed, trigger it and record the job id

# Triage a known TRAFIX queue alert

keywords: trafix, pending >, inactive (trafix), trafix.equity.stp
group: EMEA TV OPS EQUITIES
params: server
notes: Roughly 37 closed tickets across the pending/inactive variants. Every resolution note says the same thing - "dev confirmed all good, they hope to remove this error message in a future release". The alert is known noise, but somebody still has to confirm it is the known pattern and not a real backlog. That confirmation is entirely read-only.

## Steps

1. check | Read the current queue depth and whether the channel is active | risk: R0
2. check | Compare the depth against the normal range for this channel | risk: R0
3. check | Confirm messages are still flowing, so depth is not growing unbounded | risk: R0
4. notify | Post the depth and trend, and whether it matches the known benign pattern | risk: R1
5. manual | If the queue is genuinely backing up, escalate to the dev team rather than closing
