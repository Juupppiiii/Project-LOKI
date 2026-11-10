"""Index builder — writes loaded documents into the ChromaDB vector store.

One job: take a list of Documents and build/refresh the vector index. Uses the
rag vertical's index setup helpers but owns the write path.
"""

from __future__ import annotations

import logging

from llama_index.core import Document, Settings, StorageContext, VectorStoreIndex
from llama_index.core.node_parser import SentenceSplitter

from server.rag.functions.index import set_index, setup_chroma, setup_embed, setup_llm

logger = logging.getLogger(__name__)


def build_index(documents: list[Document]) -> int:
    """Build the vector index from documents and persist to ChromaDB.

    Returns the number of documents indexed.
    """

    if not documents:
        logger.warning("No documents to index.")
        return 0

    logger.info("Indexing %d documents into ChromaDB", len(documents))

    Settings.embed_model = setup_embed()
    Settings.llm = setup_llm()

    vector_store = setup_chroma()
    storage_context = StorageContext.from_defaults(vector_store=vector_store)
    splitter = SentenceSplitter(chunk_size=512, chunk_overlap=64)

    index = VectorStoreIndex.from_documents(
        documents,
        storage_context=storage_context,
        transformations=[splitter],
        show_progress=True,
    )

    set_index(index)
    logger.info("Indexed %d documents", len(documents))
    return len(documents)
