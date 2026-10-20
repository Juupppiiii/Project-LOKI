# Knowledge base

Everything BUCKY knows and recommends from, in one place.

```
knowledge/
â”œâ”€â”€ tickets/        Real solved-ticket history (the grounding for AI recommendations)
â”‚   â”œâ”€â”€ resolved_tickets.json      All pulled closed, human-resolved incidents (structured)
â”‚   â”œâ”€â”€ resolution_patterns.json   Recurring fix patterns, clustered by group/category/close-code
â”‚   â”œâ”€â”€ summary.json               Overview stats (counts, top groups, substantive rate)
â”‚   â””â”€â”€ by_group/                  One file per assignment group
â”œâ”€â”€ handbooks/      Procedures (ready to upload + publish via the Knowledge tab)
â”‚   â”œâ”€â”€ handbook_*.md              Hand-written procedures
â”‚   â””â”€â”€ auto_*.md                  Auto-drafted by Claude from the patterns above
â””â”€â”€ dictionaries/   Term/alias dictionaries that widen matching
```

## How these fit together

1. **`tickets/`** is the raw material â€” real incidents and how they were actually
   resolved this year. It is read-only grounding; the advisory LLM recommender
   (`/api/recommend-steps`) retrieves the most similar solved tickets from here
   so its suggestions cite real precedent.

2. **`handbooks/`** are the codified procedures. They are *drafts on disk* until a
   human uploads one through the Knowledge tab and publishes it â€” only then does
   it join the live rule set the matcher uses. The `auto_*.md` files were drafted
   by Claude (Opus 5 on Bedrock) from the top patterns in `tickets/`; they still
   require human review and publishing, and the risk model still classifies every
   step (destructive actions stay human-only).

3. **`dictionaries/`** map canonical terms to the ways people actually phrase them,
   widening what the matcher recognises. They affect matching only, never
   permission.

## Rebuilding the ticket knowledge

```powershell
python scripts/build_knowledge_json.py --all          # pull + structure all this year
python scripts/autodraft_procedures.py --top 8        # draft handbooks from the patterns (needs LLM)
```

Nothing here is shared between machines yet â€” it is local files. Making this a
shared, multi-user knowledge base is the AWS deployment (Aurora + ECS), which is
written but not deployed.
