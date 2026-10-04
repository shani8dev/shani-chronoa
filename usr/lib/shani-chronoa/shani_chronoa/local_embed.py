"""Memory by meaning: a small embedding model on llama.cpp, served on 127.0.0.1:8768.

Full-text search (FTS5, `conversation_store`) finds the words someone used;
"what did we say about my router?" also wants the message that said "the wifi
box keeps dropping". An embedding turns each text into a vector, and texts
that mean similar things get vectors that point the same way.

nomic-embed-text-v1.5 (Apache-2.0, 146 MB at Q8_0) is trained with a task
prefix on every input - `search_query: ` for what is asked, `search_document: `
for what is stored - and without it the vectors are measurably worse, so the
prefix is added here rather than left to each caller. Vectors are L2-normalised
here as well, so a dot product is the cosine similarity.

Nothing here is required: when the server is not set up or not answering,
`embed` returns None and callers keep their keyword search.
"""

from __future__ import annotations

import logging
import math
import os
from array import array
from pathlib import Path
from typing import Callable, List, Optional, Sequence

from shani_chronoa import files, model_service
from shani_chronoa.stt_provision import ModelSpec, file_matches, install_verified

logger = logging.getLogger(__name__)

INSTANCE = "embed"
DIMENSIONS = 768
#: tokens per input the server accepts; longer text is cut by the caller (a message, not a book)
CONTEXT = 2048
MAX_CHARS = 4000

MODEL = ModelSpec("nomic-embed-text-v1.5", "nomic-embed-text-v1.5.Q8_0.gguf", 146_146_432,
                  "3e24342164b3d94991ba9692fdc0dd08e3fd7362e0aacc396a9a5c54a544c3b7",
                  "nomic-embed-text v1.5 (Apache-2.0)",
                  "https://huggingface.co/nomic-ai/nomic-embed-text-v1.5-GGUF/resolve/"
                  "0188c9bf409793f810680a5a431e7b899c46104c")

QUERY, DOCUMENT = "search_query: ", "search_document: "


def model_dir() -> Path:
    return files.data_home() / "shani-chronoa" / "embed"


def installed() -> bool:
    return (model_dir() / MODEL.filename).is_file()


def configured() -> bool:
    return installed() and model_service.env_file(INSTANCE).is_file()


def verify() -> bool:
    return file_matches(model_dir() / MODEL.filename, MODEL)


def server_args() -> "list[str]":
    return ["-m", str(model_dir() / MODEL.filename), "--embedding", "--pooling", "mean",
            "--host", model_service.HOST, "--port", str(model_service.PORTS[INSTANCE]),
            "-c", str(CONTEXT), "-b", str(CONTEXT), "-ub", str(CONTEXT), "--no-webui",
            "-ngl", "0", "-t", str(max(1, min(4, (os.cpu_count() or 2) - 1)))]


def provision(progress: Optional[Callable[[int, int], None]] = None, config=None, transport=None) -> Path:
    from shani_chronoa import local_llm
    path = install_verified(MODEL, model_dir(), config=config, progress=progress, transport=transport, label="embed")
    model_service.write_env(INSTANCE, local_llm.server_binary() or "/usr/bin/llama-server", server_args())
    return path


def normalise(vector: Sequence[float]) -> "list[float]":
    norm = math.sqrt(sum(x * x for x in vector)) or 1.0
    return [x / norm for x in vector]


def embed(texts: Sequence[str], kind: str = DOCUMENT, timeout: float = 30.0, transport=None) -> "Optional[List[List[float]]]":
    """One unit vector per text, or None when the embedding server is not there (callers fall back)."""
    import httpx
    if not texts:
        return []
    if transport is None and not configured():
        return None
    payload = {"input": [kind + (t or " ")[:MAX_CHARS] for t in texts], "model": MODEL.key}
    try:
        with httpx.Client(timeout=httpx.Timeout(timeout, connect=3.0), transport=transport) as client:
            response = client.post(model_service.base_url(INSTANCE) + "/v1/embeddings", json=payload)
        if response.status_code != 200:
            logger.debug("embedding server answered %s: %s", response.status_code, response.text[:200])
            return None
        rows = sorted(response.json()["data"], key=lambda r: r.get("index", 0))
        vectors = [normalise(r["embedding"]) for r in rows]
    except (httpx.HTTPError, ValueError, KeyError, TypeError) as exc:
        logger.debug("no embeddings (%s: %s)", type(exc).__name__, exc)
        return None
    return vectors if len(vectors) == len(texts) else None


def to_blob(vector: Sequence[float]) -> bytes:
    return array("f", vector).tobytes()


def from_blob(blob: bytes) -> "array":
    values = array("f")
    values.frombytes(blob)
    return values


def similarity(a: Sequence[float], b: Sequence[float]) -> float:
    """Cosine similarity of two unit vectors."""
    return sum(x * y for x, y in zip(a, b))
