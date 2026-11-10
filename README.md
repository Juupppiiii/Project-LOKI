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
python -m server.ingest --knowledge path\to\knowledge

# 4. Start the server
python -m uvicorn server.api.app:app --host 127.0.0.1 --port 8800

# 5. Open http://127.0.0.1:8800 in your browser
```

## How it works

```
New ticket ──► LlamaIndex retriever (rag/)
                    │
                    ├─ ChromaDB vector search (Titan embeddings)
                    │   → top-K similar resolved tickets with resolutions
                    │
                    ▼
               Claude synthesis (recommend/)
                    │
                    └─ Cited resolution recommendation
                         → engineer reads it, does the work themselves
```

## Project structure (ULTRON-style verticals)

```
server/                          The Python package
├── config.py                    Shared configuration (.env)
├── models.py                    Shared Pydantic request/response models
│
├── api/                         HTTP boundary (its own process)
│   ├── app.py                   FastAPI factory, middleware, lifespan
│   └── routes.py                POST /api/query, GET /api/health, GET /api/index/status
│
├── rag/                         Vector index + document conversion + search
│   └── functions/
│       ├── document.py          ticket dict → LlamaIndex Document
│       ├── index.py             ChromaDB index management (get/set/reset)
│       └── search.py            query → list[ResolvedTicket]
│
├── recommend/                   LLM resolution synthesis
│   └── functions/
│       ├── prompt.py            System + user prompt construction
│       ├── synthesize.py        Claude call → Recommendation
│       └── confidence.py        Confidence label extraction
│
├── ingest/                      Corpus loading CLI (its own process)
│   ├── main.py                  python -m server.ingest
│   └── functions/
│       ├── loader.py            Read knowledge JSON dirs / flat files
│       └── indexer.py           Build the ChromaDB vector index
│
└── tests/                       Tests mirror the verticals
    ├── test_api.py
    ├── test_document.py
    ├── test_confidence.py
    └── test_models.py

dashboard/                       React + Vite UI (macOS-inspired)
docs/                            Architecture documentation
```

Each vertical has **one job**. They communicate through typed returns
(Pydantic models), not shared globals. The `functions/` sub-packages hold the
logic; the package `__init__.py` describes the boundary.

## API

| Method | Path | Description |
|---|---|---|
| `POST` | `/api/query` | Submit a ticket, get similar resolutions + AI recommendation |
| `GET` | `/api/health` | Health check |
| `GET` | `/api/index/status` | Vector index status and document count |

## Development

```bash
pip install -r requirements-dev.txt
python -m pytest -q
python -m ruff check server
```

## Data backup

Your historical ticket corpus is backed up at:
`C:\Users\d_jupiterdimalibot\bucky_data_backup\`

To ingest it:
```bash
python -m server.ingest --knowledge C:\Users\d_jupiterdimalibot\bucky_data_backup\knowledge
```
