# Fix a hung desktop application or printer

keywords: application hung, ICE process stuck, restart application, missing Broker toolkit icon, shortcut missing, print anywhere, printer not working, Teams VDI install failure, tpENERGYTRADE missing, export import settings
group: TPICAP NA DesktopSupport
params: username, hostname, application_name, printer_name
notes: Auto-drafted from 595 similar resolutions (e.g. INC73438669, INC73438699, INC73438709, INC73438675, INC73438703). Advisory draft; human review required.

## Steps

1. check | Confirm with user the affected application/device, hostname and exact symptom (app unresponsive, missing icon, printer failure, failed install, missing profile/config) | risk: R0
2. connect | Remote into the user's workstation or VDI session (hostname) with the user present | risk: R0
3. check | Review running processes and application/installer logs for the affected app (e.g. multiple ICE processes, Teams VDI install errors); confirm shortcut, printer mapping and profile/config files exist | risk: R0
4. run_command | For hung applications, stop the orphaned/duplicate application processes for the user session, then relaunch the application and confirm it loads | risk: R3
5. manual | For missing items, re-create the application shortcut/icon (e.g. Broker toolkit) or export the required profile/config from the old workstation and import it on the new one (e.g. tpENERGYTRADE), then validate with the user | risk: R3
6. manual | For printing issues, remove the stale printer link and install the Print Anywhere / correct queue, then run a test print with the user | risk: R3
7. notify | If the fix requires another team (e.g. GDM for Teams VDI installation failures), engage them, then confirm resolution with the user and close as Solved (Work Around) with actions taken in work notes
