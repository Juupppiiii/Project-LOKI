# Reclaim disk space on a Linux host

keywords: disk capacity issues, for device, filesystem is above, disk space, /var, /cores
group: Global Linux Support
params: server, device
notes: |
  24 closed tickets (QA and UAT hosts). The hostname is extractable from every
  one of them, and the resolution notes contain the actual df output engineers
  pasted in, plus "cleared cores", "cleared core files", "done cleanup".

  Deliberate split: the agent measures and reports, a person decides what to
  delete. Deleting files is not a first thing to hand over, and the notes show
  the decision varies by device - /cores is safe to clear, /var often is not.

## Steps

1. check | Read current usage for the reported device | command: df -h {device} | risk: R0
2. check | Find the largest directories under the device | command: du -xh {device} --max-depth=2 | sort -rh | head -15 | risk: R0
3. check | Look for core dumps, which are usually safe to clear | command: find {device} -name 'core.*' -type f -mtime +1 -printf '%s %p\n' | risk: R0
4. check | Look for rotated and compressed logs | command: find {device} -name '*.gz' -o -name '*.[0-9]' -type f -mtime +7 | risk: R0
5. notify | Post the usage, the biggest consumers, and what is safely reclaimable | risk: R1
6. manual | Decide what may be deleted on this device, then clear it
7. check | Confirm usage is back below the alert threshold | command: df -h {device} | risk: R0
8. notify | Record the space recovered | risk: R1
