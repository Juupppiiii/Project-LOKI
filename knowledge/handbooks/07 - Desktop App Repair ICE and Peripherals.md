# Repair desktop apps (ICE, Excel feeds, peripherals)

keywords: ICE charts not displaying, ICE XL feeds into Excel, ICE Launcher, signature editing, webcam not working, WhatsApp Web reconnect, LeapXpert reconnect, application reinstall, app not working after update
group: TP ICAP End User Services Verde
params: user_name, hostname, application_name
notes: Auto-drafted from 284 similar resolutions (e.g. INC73438756, INC73438745, INC73438810, INC73438790, INC73421568). Advisory draft; human review required.

## Steps

1. check | Confirm with <user_name> which application is affected (<application_name>) and the exact symptom — e.g. ICE charts on wrong monitor, ICE XL feeds not populating in Excel, signature editing blocked, webcam not detected, WhatsApp Web / LeapXpert disconnected from work phone | risk: R0
2. connect | Remote onto <hostname> with the user present so the behaviour can be reproduced and confirmed live | risk: R0
3. check | Review the application's own settings/configuration for the reported symptom (ICE chart/monitor layout and on-board graphics output, ICE XL add-in enabled in Excel, webcam device present and not disabled, messaging app paired to work phone) | risk: R0
4. run_command | Apply the known quick fix for the symptom where one exists (e.g. run the ICE Launcher / ICE XL repair to restore Excel feeds, apply the signature editing quick fix, re-pair WhatsApp Web / LeapXpert by scanning the code from the work phone) and confirm with the user that it now works | risk: R1
5. restart_service | Restart the affected application (and Excel if add-in related) and re-test the feed, chart layout or device with the user still on the call | risk: R1
6. manual | If the fault persists, uninstall and reinstall <application_name> in full, then re-apply user settings and re-test — destructive to local app config and profiles, human approval required | risk: R3
7. notify | If still unresolved after reinstall, escalate to the application SME / senior EUS engineer with the symptom, steps already tried and reinstall outcome; otherwise record the fix in the work notes and close as Solved (Permanently) | risk: R0
