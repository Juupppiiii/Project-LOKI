# Unlock a locked account

keywords: account locked, unlock account, account is locked, locked out
group: Global Service Desk
params: username
notes: Drafted from recurring close notes such as "AD account unlocked" and "The user account was found to be locked in Active Directory and has been unlocked."

## Steps

1. check | Confirm the account is actually locked | command: Search-ADAccount -LockedOut -Identity {username} | risk: R0
2. check | Confirm the request came from the account owner or their manager | risk: R0
3. run_command | Unlock the account | command: Unlock-ADAccount -Identity {username}
4. check | Confirm the account is no longer locked | command: Search-ADAccount -LockedOut -Identity {username} | risk: R0
5. notify | Tell the requester the account is unlocked and ask them to retry

# Investigate a GLUE-CDP issue

keywords: glue-cdp, issue encountered with glue-cdp, cdp production
group: CDP
params: server
notes: High volume in the 3-month extract (817 tickets). Most close notes were "replayed", so the replay decision is the judgement call and stays with a human.

## Steps

1. check | Confirm the pipeline is failing and capture the error | risk: R0
2. check | Identify the affected batch or message | risk: R0
3. manual | Decide whether a replay is safe, or whether the payload needs fixing first
4. run_command | Replay the failed batch | command: Invoke-CdpReplay -Batch {server}
5. check | Confirm the replay succeeded and no messages remain pending | risk: R0
6. notify | Record the replay and outcome on the ticket
