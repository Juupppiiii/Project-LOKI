# Bucky — IT Ticket Resolution RAG

A ticket comes in. Bucky instantly retrieves the most similar resolved tickets
from your historical corpus and asks Claude to synthesize the best resolution
approach — with citations. The engineer reads it and does the work; the AI saves
them the research time.

Built on **LlamaIndex + ChromaDB + Amazon Bedrock (Claude) + FastAPI + React**.

## Quick start

```bash
# 1. Create a virtual environment and install dependencies
python -m venv .venv
.venv\Scripts\activate         # Windows
pip install -r requirements.txt

# 2. Copy .env.example to .env and fill in your AWS / ServiceNow credentials
copy .env.example .env

# 3. Ingest your resolved ticket corpus into the vector store
python -m bucky.ingest --knowledge path\to\knowledge

# 4. Start the server
python -m uvicorn bucky.api:app --host 127.0.0.1 --port 8800

# 5. Open http://127.0.0.1:8800 in your browser
```

## How it works

```
New ticket ──► LlamaIndex retriever
                    │
                    ├─ ChromaDB vector search (Titan embeddings)
                    │   → top-K similar resolved tickets with resolutions
                    │
                    ▼
               Claude (Bedrock Converse)
                    │
                    └─ Synthesize a cited resolution recommendation
                         → engineer reads it, does the work themselves
```

1. **Ingest** — `python -m bucky.ingest` reads your resolved ticket JSON
   (the structured `knowledge/tickets/by_group/*.json` corpus or a flat
   `resolved_tickets.json`), splits the text, embeds it with Amazon Titan, and
   stores it in a local ChromaDB vector database.

2. **Query** — when you submit a ticket (via the UI or `POST /api/query`), the
   retriever finds the most similar historical resolutions by vector similarity.

3. **Synthesize** — Claude reads the retrieved evidence and your ticket, then
   writes a grounded recommendation with clear steps and citations. If the
   evidence is weak, it says so.

## Project structure

```
bucky/              The Python package
  api.py            FastAPI server (POST /api/query, GET /api/health)
  rag.py            RAG engine: ingest, retrieve, synthesize
  models.py         Pydantic request/response models
  config.py         Environment configuration
  ingest.py         CLI: python -m bucky.ingest

ui/                 React + Vite frontend
  src/App.jsx       The search-and-results interface
  src/styles.css    Dark theme UI styles

tests/              Pytest suite
data/               Git-ignored: raw ticket CSVs, vector index (ChromaDB)
```

## API

| Method | Path | Description |
|---|---|---|
| `POST` | `/api/query` | Submit a ticket, get similar resolutions + AI recommendation |
| `GET` | `/api/health` | Health check |
| `GET` | `/api/index/status` | Vector index status and document count |

## Configuration

| Variable | Default | Purpose |
|---|---|---|
| `AWS_REGION` | `eu-west-1` | AWS region for Bedrock |
| `BEDROCK_MODEL_ID` | `eu.anthropic.claude-opus-5` | Claude model for synthesis |
| `SN_INSTANCE` | — | ServiceNow instance (for future live pulling) |
| `CHROMA_PERSIST_DIR` | `data/index/chroma` | Where ChromaDB stores the index |
| `BUCKY_TOP_K` | `8` | Number of similar tickets to retrieve |
| `BUCKY_SIMILARITY_THRESHOLD` | `0.3` | Minimum similarity to include a result |

## Data backup

Your historical ticket corpus (22k resolved tickets) is backed up at:
`C:\Users\d_jupiterdimalibot\bucky_data_backup\`

To ingest it:
```bash
python -m bucky.ingest --knowledge C:\Users\d_jupiterdimalibot\bucky_data_backup\knowledge
```

## Development

```bash
pip install -r requirements-dev.txt
python -m pytest -q
python -m ruff check bucky tests
```
