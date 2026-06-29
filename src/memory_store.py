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
# INTERNAL MEMORY WRITE
# =========================================================

def _add_memory_internal(
    role: str,
    content: str,
):

    global pending_writes

    logging.info(
        f"[_add_memory_internal] Start | role={role}"
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

        metadata.append({
            "role": role,
            "content": content,
            "timestamp": time.time(),
        })

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
):

    logging.info(
        f"[add_memory] Called | "
        f"role={repr(role)} | "
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
    )

    logging.info(
        f"[add_memory] Queueing item: "
        f"{repr(queue_item)[:300]}"
    )

    memory_queue.put(queue_item)

    logging.info(
        "[add_memory] Queue insert complete"
    )

# =========================================================
# SEARCH MEMORY
# =========================================================

def search_memory(
    query: str,
    top_k: int = 5,
):

    logging.info(
        f"[search_memory] Query={query}"
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

    for idx in indices[0]:

        if idx < 0:
            continue

        if idx >= len(metadata):
            continue

        results.append(
            metadata[idx]
        )

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

            if len(item) != 2:

                logging.error(
                    f"[MemoryWorker] Invalid tuple length: "
                    f"{len(item)}"
                )

                continue

            role, content = item

            logging.info(
                f"[MemoryWorker] Processing | "
                f"role={role} | "
                f"content_len={len(content)}"
            )

            _add_memory_internal(
                role,
                content,
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