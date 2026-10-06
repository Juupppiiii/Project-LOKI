# Rebuild an Outlook profile or fix third-party access

keywords: recreate outlook profile, outlook profile corrupt, missing folder in outlook, restore from deleted items, okta verify setup, third party application access, finra traqs, trace okta account, application off-screen, application window not visible
group: TPICAP NA DesktopSupport
params: user_name, device_name, application_name
notes: Auto-drafted from 264 similar resolutions (e.g. INC73400693, INC73390214, INC73391518, INC73374199, INC73382594). Advisory draft; human review required.

## Steps

1. check | Contact the user and confirm the reported symptom and scope: Outlook profile/mail issue, a missing mailbox folder or items, a third-party application login (e.g. OKTA Verify, FINRA TRAQS/TRACE), or an application window that is not visible on screen. Record device_name and application_name. | risk: R0
2. connect | Remote into the user's workstation (device_name) with the user present to observe the issue live. | risk: R0
3. check | For display complaints: check whether the application is running but positioned off-screen (multiple/undocked monitors, Task Manager / taskbar shows the process). If found, unhide/move the application window back onto the primary display and confirm with the user. | risk: R0
4. check | For missing mail folders or items: check Outlook 'Deleted Items' and 'Recoverable Items' for the folder or messages. If present, restore the folder/items back to the inbox with the user watching and confirm the content is complete. | risk: R0
5. check | For Outlook profile symptoms (repeated password prompts, profile won't load, corrupt OST): verify credentials and connectivity first, then confirm with the user that mail is cached/online before any profile change. | risk: R0
6. delete_profile | With user approval, remove and recreate the Outlook mail profile in Control Panel > Mail, re-add the account, allow the mailbox to resync, and verify mail, calendar and shared mailboxes. Destructive — local cached data (OST, local autocomplete, signatures) may be lost; human-executed only. | risk: R3
7. notify | For third-party application access (OKTA Verify, FINRA TRAQS/TRACE and similar vendor-managed accounts): confirm the account is vendor-controlled, then direct the user to the third party/vendor support to issue or reset the account and complete enrollment. Document the vendor contact and outcome in work notes. | risk: R0
8. manual | Confirm resolution with the user, note which path resolved the issue (window unhidden, items restored, profile recreated, or vendor-provisioned access),
