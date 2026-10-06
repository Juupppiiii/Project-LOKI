# Fix Outlook, mailbox, or password issues

keywords: cannot access outlook, outlook not opening, OOO auto replies, out of office still sending, mailbox access issue, teams cache, workspace reboot, password reset email
group: Global Service Desk
params: user_id, user_email, workspace_id
notes: Auto-drafted from 625 similar resolutions (e.g. INC73429987, INC73428676, INC73406318, INC73406391, INC73397326). Advisory draft; human review required.

## Steps

1. check | Confirm reported symptom with the user: Outlook will not open/authenticate, or out-of-office auto-replies are still being sent after being turned off. Capture user_id, user_email and affected device/Amazon Workspace id. | risk: R0
2. check | Verify account state for user_email: account enabled, not locked, password not expired, and primary email address correct in the directory. | risk: R0
3. check | Run a message trace and review Mimecast held/queued messages for user_email to confirm whether OOO automatic replies are still leaving the mailbox and whether anything looks suspicious. | risk: R0
4. check | Confirm the OOO / automatic reply setting is actually disabled on the mailbox (server side), so any remaining replies can be attributed to a stale client cache. | risk: R0
5. manual | If the mailbox is correct but the client still shows OOO or cannot sign in, reset the user's password with them on the call and re-verify the email address; have the user sign back in to Outlook. | risk: R3
6. reboot | If the client remains stale after sign-in (typical Teams/Outlook cache issue), reboot the user's Amazon Workspace (workspace_id) with user consent and confirm all work is saved first. | risk: R3
7. notify | Ask the user to confirm Outlook access and that OOO replies have stopped; if the message trace showed anything unexplained, pass the findings to the ITSEC team for a second review, then close as Solved (Permanently).
