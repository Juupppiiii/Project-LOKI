# Scripts

Operational one-off tools. Run from the project root:

```powershell
python scripts/01_pull_closed_incidents.py --limit 5000
```

## Knowledge building (the main pipeline)

| # | Script | What it does |
|---|---|---|
| 01 | `01_pull_closed_incidents.py` | Pull closed, human-resolved incidents from ServiceNow (read-only) |
| 02 | `02_build_knowledge_json.py` | Structure the pulled tickets into JSON knowledge files (`knowledge/tickets/`) |
| 03 | `03_mine_resolutions.py` | Cluster resolution patterns from the tickets; `--exclude-group` to filter noise |
| 04 | `04_autodraft_procedures.py` | Use Claude (Opus 5 on Bedrock) to draft handbook procedures from the patterns |
| 05 | `05_compare_matchers.py` | Shadow-compare keyword vs semantic matchers on real tickets (changes nothing) |

## Operations

| # | Script | What it does |
|---|---|---|
| 06 | `06_demo_agent_run.py` | Drive propose → approve → execute → audit end-to-end in simulation |
| — | `migrate_sqlite_to_postgres.py` | Migrate local SQLite data to PostgreSQL (dry-run by default) |

## Documentation builders

| # | Script | What it does |
|---|---|---|
| 08 | `08_build_architecture_pdf.py` | Generate the architecture PDF in `output/` |
| 09 | `09_build_architecture_diagram_pdf.py` | Generate the architecture diagram PDF |
| 10 | `10_build_powerpoint.py` | Generate the PowerPoint overview deck |

## Notes

- All scripts resolve the project root from their own location (`__file__`),
  so they work from any working directory.
- `migrate_sqlite_to_postgres.py` keeps its flat name because it is imported
  by `tests/test_migration.py` and must be a valid Python module name.
- The knowledge-building pipeline runs 01 → 02 → 03 → 04 in order. Step 04
  requires a live Bedrock model (`LOKI_LLM_ENABLED=1` + AWS creds).
