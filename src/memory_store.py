import os
import time
import pickle
import threading
from typing import List, Dict, Any, Optional

import numpy as np
import faiss
from dotenv import load_dotenv
from google import genai

load_dotenv()

# =========================================================
# CONFIG
# =========================================================

INDEX_PATH = "memory/faiss.index"
META_PATH = "memory/meta.pkl"

EMBED_MODEL = "gemini-embedding-001"
EMBED_DIM = 3072

TOP_K_DEFAULT = 3
SIMILARITY_THRESHOLD = 0.82

os.makedirs("memory", exist_ok=True)

# =========================================================
# GEMINI CLIENT
# =========================================================

client = genai.Client(api_key=os.getenv("GEMINI_API_KEY"))

# =========================================================
# LOAD INDEX
# =========================================================

if os.path.exists(INDEX_PATH):
    index = faiss.read_index(INDEX_PATH)
    with open(META_PATH, "rb") as f:
        metadata = pickle.load(f)
else:
    index = faiss.IndexFlatIP(EMBED_DIM)
    metadata = []

lock = threading.Lock()

# =========================================================
# EMBEDDING
# =========================================================

def embed(text: str) -> np.ndarray:
    res = client.models.embed_content(
        model=EMBED_MODEL,
        contents=text
    )

    vec = np.array(res.embeddings[0].values, dtype="float32").reshape(1, -1)
    faiss.normalize_L2(vec)
    return vec

# =========================================================
# STORAGE FILTER (STRICT)
# =========================================================

def should_store(text: str) -> bool:
    if not isinstance(text, str):
        return False

    t = text.strip().lower()

    if len(t) < 60:
        return False

    blocked = [
        "click",
        "typing",
        "opening",
        "launching",
        "tool called",
        "executing",
        "error",
        "traceback",
        "exception"
    ]

    return not any(b in t for b in blocked)

# =========================================================
# MEMORY WRITE (ONLY SUMMARIES)
# =========================================================

def add_memory(role: str, content: str, category: str = "task_summary", tags=None, metadata_fields=None, source: str | None = None):

    if not should_store(content):
        return

    vec = embed(content)

    with lock:
        index.add(vec)

        metadata.append({
            "content": content,
            "role": role,
            "category": category,
            "timestamp": time.time()
        })

        # persist immediately (simple + safe)
        faiss.write_index(index, INDEX_PATH)
        with open(META_PATH, "wb") as f:
            pickle.dump(metadata, f)

# =========================================================
# MEMORY SEARCH (GATED + CLEAN)
# =========================================================

def search_memory(query: str, top_k: int = TOP_K_DEFAULT) -> List[Dict[str, Any]]:

    if len(metadata) == 0:
        return []

    qvec = embed(query)

    with lock:
        scores, indices = index.search(qvec, top_k * 3)

    results = []

    for score, idx in zip(scores[0], indices[0]):

        if idx < 0 or idx >= len(metadata):
            continue

        if score < SIMILARITY_THRESHOLD:
            continue

        item = metadata[idx]

        results.append(item)

        if len(results) >= top_k:
            break

    return results

# =========================================================
# SAFE MEMORY CONTEXT BUILDER
# =========================================================

def build_memory_context(query: str) -> str:

    memories = search_memory(query)

    if not memories:
        return ""

    return "\n".join(
        f"- {m['content']}" for m in memories
    )

# =========================================================
# COMPATIBILITY LAYER (RESTORE PUBLIC API)
# =========================================================

def record_user_preference(content: str, tags=None, metadata_fields=None, source: str | None = None):
    return add_memory(
        role="user",
        content=content,
        category="user_preference",
        tags=tags,
        metadata_fields=metadata_fields,
        source=source or "user",
    )


def record_failed_attempt(content: str, tags=None, metadata_fields=None, source: str | None = None):
    return add_memory(
        role="assistant",
        content=content,
        category="failed_attempt",
        tags=tags,
        metadata_fields=metadata_fields,
        source=source or "system",
    )


def record_feedback(content: str, tags=None, metadata_fields=None, source: str | None = None):
    return add_memory(
        role="user",
        content=content,
        category="feedback",
        tags=tags,
        metadata_fields=metadata_fields,
        source=source or "user",
    )