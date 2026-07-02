# ai/memory_store.py

import os
import time
import pickle
import queue
import threading
import logging

import numpy as np
import faiss

from dotenv import load_dotenv
from google import genai

load_dotenv()

# =========================================================
# LOGGING
# =========================================================


# =========================================================
# CONFIG
# =========================================================

INDEX_PATH = "memory/faiss.index"
META_PATH = "memory/meta.pkl"

EMBED_MODEL = "gemini-embedding-001"

EMBED_DIM = 3072

SAVE_INTERVAL = 5

os.makedirs("memory", exist_ok=True)

# =========================================================
# GEMINI CLIENT
# =========================================================

client = genai.Client(
    api_key=os.getenv("GEMINI_API_KEY")
)

# =========================================================
# LOAD / CREATE INDEX
# =========================================================

if os.path.exists(INDEX_PATH):

    logging.info(
        f"[Memory] Loading existing FAISS index: {INDEX_PATH}"
    )

    index = faiss.read_index(INDEX_PATH)

    with open(META_PATH, "rb") as f:
        metadata = pickle.load(f)

    logging.info(
        f"[Memory] Loaded {len(metadata)} memories"
    )

else:

    logging.info(
        "[Memory] Creating new FAISS index"
    )

    # Cosine similarity
    index = faiss.IndexFlatIP(EMBED_DIM)

    metadata = []

# =========================================================
# THREADING
# =========================================================

memory_queue = queue.Queue()

memory_lock = threading.Lock()

pending_writes = 0

# =========================================================
# EMBEDDING FUNCTION
# =========================================================

def get_embedding(text: str):

    logging.info(
        f"[Embedding] Request | text_len={len(text)}"
    )

    response = client.models.embed_content(
        model=EMBED_MODEL,
        contents=text,
    )

    vector = response.embeddings[0].values

    logging.info(
        f"[Embedding] Response dimension={len(vector)}"
    )

    embedding = np.array(
        vector,
        dtype="float32",
    )

    embedding = embedding.reshape(1, -1)

    # Normalize for cosine similarity
    faiss.normalize_L2(embedding)

    logging.info(
        f"[Embedding] Final shape={embedding.shape}"
    )

    return embedding

# =========================================================
# MEMORY FILTER
# =========================================================

def should_store_memory(text: str):

    if not isinstance(text, str):
        return False

    text = text.strip()

    if len(text) < 20:
        return False

    blocked = [
        "traceback",
        "exception",
        "<html",
        "tool returned",
        "screenshot",
    ]

    lower = text.lower()

    return not any(
        b in lower
        for b in blocked
    )

# =========================================================
# MEMORY HELPERS
# =========================================================

DEFAULT_MEMORY_CATEGORY = "generic"

ALLOWED_MEMORY_CATEGORIES = {
    "generic",
    "user_preference",
    "user_query",
    "assistant_response",
    "failed_attempt",
    "feedback",
    "task_summary",
}


def _normalize_category(category):
    if not isinstance(category, str):
        return DEFAULT_MEMORY_CATEGORY

    category = category.strip().lower()
    if category in ALLOWED_MEMORY_CATEGORIES:
        return category

    return DEFAULT_MEMORY_CATEGORY


def _build_memory_entry(
    role: str,
    content: str,
    category: str = DEFAULT_MEMORY_CATEGORY,
    tags=None,
    metadata_fields=None,
    source: str | None = None,
):
    return {
        "role": role,
        "content": content,
        "category": _normalize_category(category),
        "tags": list(tags) if isinstance(tags, (list, tuple)) else [],
        "metadata": metadata_fields if isinstance(metadata_fields, dict) else {},
        "source": source or "system",
        "timestamp": time.time(),
    }

# =========================================================
# INTERNAL MEMORY WRITE
# =========================================================

def _add_memory_internal(
    role: str,
    content: str,
    category: str = DEFAULT_MEMORY_CATEGORY,
    tags=None,
    metadata_fields=None,
    source: str | None = None,
):

    global pending_writes

    logging.info(
        f"[_add_memory_internal] Start | role={role} | category={category}"
    )

    if not should_store_memory(content):

        logging.info(
            "[_add_memory_internal] Filtered"
        )

        return

    embedding = get_embedding(content)

    with memory_lock:

        logging.info(
            "[_add_memory_internal] Adding embedding to FAISS"
        )

        index.add(embedding)

        logging.info(
            "[_add_memory_internal] Updating metadata"
        )

        metadata.append(
            _build_memory_entry(
                role,
                content,
                category=category,
                tags=tags,
                metadata_fields=metadata_fields,
                source=source,
            )
        )

        pending_writes += 1

        logging.info(
            f"[_add_memory_internal] pending_writes={pending_writes}"
        )

        # Periodic disk persistence
        if pending_writes >= SAVE_INTERVAL:

            logging.info(
                "[_add_memory_internal] Saving FAISS index"
            )

            faiss.write_index(
                index,
                INDEX_PATH,
            )

            logging.info(
                "[_add_memory_internal] Saving metadata"
            )

            with open(META_PATH, "wb") as f:

                pickle.dump(
                    metadata,
                    f,
                )

            pending_writes = 0

            logging.info(
                "[_add_memory_internal] Save complete"
            )

# =========================================================
# PUBLIC MEMORY ADD
# =========================================================

def add_memory(
    role: str,
    content: str,
    category: str = DEFAULT_MEMORY_CATEGORY,
    tags=None,
    metadata_fields=None,
    source: str | None = None,
):

    logging.info(
        f"[add_memory] Called | "
        f"role={repr(role)} | "
        f"category={repr(category)} | "
        f"content_type={type(content)}"
    )

    if not isinstance(role, str):

        logging.error(
            "[add_memory] role is not string"
        )

        return

    if not isinstance(content, str):

        logging.error(
            "[add_memory] content is not string"
        )

        return

    if not should_store_memory(content):

        logging.info(
            "[add_memory] Memory filtered"
        )

        return

    queue_item = (
        role,
        content,
        category,
        tags,
        metadata_fields,
        source,
    )

    logging.info(
        f"[add_memory] Queueing item: "
        f"{repr(queue_item)[:300]}"
    )

    memory_queue.put(queue_item)

    logging.info(
        "[add_memory] Queue insert complete"
    )


def record_user_preference(
    content: str,
    tags=None,
    metadata_fields=None,
    source: str | None = None,
):
    add_memory(
        role="user",
        content=content,
        category="user_preference",
        tags=tags,
        metadata_fields=metadata_fields,
        source=source or "user",
    )


def record_failed_attempt(
    content: str,
    tags=None,
    metadata_fields=None,
    source: str | None = None,
):
    add_memory(
        role="assistant",
        content=content,
        category="failed_attempt",
        tags=tags,
        metadata_fields=metadata_fields,
        source=source or "user",
    )


def record_feedback(
    content: str,
    tags=None,
    metadata_fields=None,
    source: str | None = None,
):
    add_memory(
        role="user",
        content=content,
        category="feedback",
        tags=tags,
        metadata_fields=metadata_fields,
        source=source or "user",
    )

# =========================================================
# SEARCH MEMORY
# =========================================================

def search_memory(
    query: str,
    top_k: int = 5,
    categories=None,
    tags=None,
):

    logging.info(
        f"[search_memory] Query={query} | categories={categories} | tags={tags}"
    )

    if len(metadata) == 0:

        logging.info(
            "[search_memory] No memories stored"
        )

        return []

    embedding = get_embedding(query)

    with memory_lock:

        logging.info(
            "[search_memory] Searching FAISS"
        )

        distances, indices = index.search(
            embedding,
            top_k,
        )

    results = []
    filter_categories = {
        _normalize_category(c)
        for c in (categories or [])
        if isinstance(c, str)
    }

    filter_tags = set(
        t for t in (tags or [])
        if isinstance(t, str)
    )

    for idx in indices[0]:

        if idx < 0:
            continue

        if idx >= len(metadata):
            continue

        item = metadata[idx]

        if filter_categories and item.get("category") not in filter_categories:
            continue

        if filter_tags:
            item_tags = set(item.get("tags", []))
            if not item_tags.intersection(filter_tags):
                continue

        results.append(item)

    logging.info(
        f"[search_memory] Retrieved {len(results)} memories"
    )

    return results

# =========================================================
# MEMORY WORKER
# =========================================================

def memory_worker():

    logging.info(
        "[MemoryWorker] Started"
    )

    while True:

        item = None

        try:

            logging.info(
                "[MemoryWorker] Waiting for queue item..."
            )

            item = memory_queue.get()

            logging.info(
                f"[MemoryWorker] Raw item | "
                f"type={type(item)} | "
                f"value={repr(item)[:300]}"
            )

            if not isinstance(item, tuple):

                logging.error(
                    "[MemoryWorker] Item is not tuple"
                )

                continue

            if len(item) != 6:

                logging.error(
                    f"[MemoryWorker] Invalid tuple length: "
                    f"{len(item)}"
                )

                continue

            role, content, category, tags, metadata_fields, source = item

            logging.info(
                f"[MemoryWorker] Processing | "
                f"role={role} | "
                f"category={category} | "
                f"content_len={len(content)}"
            )

            _add_memory_internal(
                role,
                content,
                category=category,
                tags=tags,
                metadata_fields=metadata_fields,
                source=source,
            )

            logging.info(
                "[MemoryWorker] Memory stored"
            )

        except Exception as e:

            logging.exception(
                f"[MemoryWorker] Fatal error: {e}"
            )

        finally:

            try:

                if item is not None:

                    memory_queue.task_done()

            except Exception as e:

                logging.exception(
                    f"[MemoryWorker] task_done failed: {e}"
                )

# =========================================================
# FORCE SAVE
# =========================================================

def flush_memory_to_disk():

    with memory_lock:

        logging.info(
            "[flush_memory_to_disk] Saving FAISS index"
        )

        faiss.write_index(
            index,
            INDEX_PATH,
        )

        logging.info(
            "[flush_memory_to_disk] Saving metadata"
        )

        with open(META_PATH, "wb") as f:

            pickle.dump(
                metadata,
                f,
            )

        logging.info(
            "[flush_memory_to_disk] Complete"
        )

# =========================================================
# START WORKER
# =========================================================

worker_thread = threading.Thread(
    target=memory_worker,
    daemon=True,
)

worker_thread.start()

logging.info(
    "[Memory] Worker thread started"
)