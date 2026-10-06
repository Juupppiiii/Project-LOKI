# Validate a CMK host alert

keywords: Events-CMK, CMK alert, host down, check_mk notification, vcenter warning, icmp unreachable, ping failed, transient event
group: Global Wintel Server Support
params: hostname, vcenter_region, vcenter_url
notes: Auto-drafted from 659 similar resolutions (e.g. INC72845741, INC72845783, INC72845648, INC72845734, INC72845630). Advisory draft; human review required.

## Steps

1. check | Review the CMK event details for `hostname` — alert service, state, and timestamp — and confirm whether the alert has already auto-recovered in the monitoring console | risk: R0
2. connect | Log in to vCenter (`vcenter_url`) for the applicable region to review host/VM state | risk: R0
3. check | In vCenter, check `hostname` and the wider cluster/region for triggered alarms, host connection state, and power state; confirm there is no bulk warning or regional issue | risk: R0
4. check | Repeat the vCenter review across all remaining regions to rule out a multi-region or platform-wide event | risk: R0
5. run_command | Perform ICMP testing against `hostname` (e.g. `ping -n 10 <hostname>`) and record packet loss and latency results | risk: R0
6. manual | Attach vCenter check and ICMP test evidence to the ticket; if vCenter is clean and ICMP replies normally, close the ticket (and related duplicate CMK tickets in bulk) noting the alert as transient | risk: R3
7. notify | If vCenter shows a genuine alarm or ICMP still fails, escalate to Global Wintel Server Support / virtualisation on-call with the collected evidence instead of closing
