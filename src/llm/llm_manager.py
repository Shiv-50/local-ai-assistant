# src/llm/llm_manager.py

import requests
from threading import Lock

from langchain_ollama import ChatOllama
from langchain_google_genai import ChatGoogleGenerativeAI
import os
from dotenv import load_dotenv

from src.utils.logger import get_logger, TimedBlock
from src.utils.timeout import TIMEOUTS

load_dotenv()

log = get_logger(__name__)


class LLMManager:
    """
    Production-grade local Ollama model manager.

    Features:
    - Thread-safe model access
    - Automatic unloading of heavy models
    - Router model preloading
    - Shared reusable model cache
    - Prevents VRAM explosion
    """

    OLLAMA_BASE = os.getenv("OLLAMA_BASE", "http://127.0.0.1:11434")

    def __init__(self):
        self._models: dict[str, object] = {}
        self._active_model: str | None = None
        self._lock = Lock()
        self._preloaded_models: set[str] = set()

    # =====================================================
    # CREATE MODEL
    # =====================================================

    def _create_model(
        self,
        model_family: str = "ollama",
        model_name: str = "qwen2.5:7b",
        temperature: float = 0.0,
        timeout: int = TIMEOUTS.LLM_INFERENCE,
        num_predict: int = 1024,
        format: str = None,
    ):
        log.info("model.create", model_family=model_family, model_name=model_name,
                 temperature=temperature, timeout=timeout, num_predict=num_predict)

        if model_family == "ollama":
            return ChatOllama(
                model=model_name,
                temperature=temperature,
                timeout=timeout,          # seconds until first token
                num_predict=num_predict,
                format=format,
                num_ctx=4096,
                num_thread=8,
                keep_alive="20m",
            )

        elif model_family == "google":
            api_key = os.getenv("GEMINI_API_KEY")
            if not api_key:
                raise ValueError("GEMINI_API_KEY not set in environment variables")
            return ChatGoogleGenerativeAI(
                model=model_name,
                temperature=temperature,
                timeout=timeout,
            )

        else:
            raise ValueError(f"Unknown model_family: {model_family!r}")

    # =====================================================
    # UNLOAD MODEL
    # =====================================================

    def _unload(self, model_name: str):
        try:
            requests.post(
                f"{self.OLLAMA_BASE}/api/generate",
                json={"model": model_name, "keep_alive": 0},
                timeout=TIMEOUTS.OLLAMA_UNLOAD,
            )
            log.info("model.unloaded", model_name=model_name)
        except Exception as e:
            log.warning("model.unload_failed", model_name=model_name, error=str(e))

    # =====================================================
    # GET MODEL
    # =====================================================

    def get_model(
        self,
        model_family: str = "ollama",
        model_name: str = "qwen2.5:7b",
        temperature: float = 0.0,
        timeout: int = TIMEOUTS.LLM_INFERENCE,
        num_predict: int = 1024,
        preload: bool = False,
        format: str = None,
    ):
        cache_key = f"{model_name}_{temperature}_{num_predict}_{format}"

        with self._lock:

            # ── Return cached instance ────────────────────────────
            if cache_key in self._models:
                log.debug("model.cache_hit", model_name=model_name)
                self._active_model = model_name
                return self._models[cache_key]

            # ── Unload previous heavy model ───────────────────────
            if (
                self._active_model
                and self._active_model != model_name
                and self._active_model not in self._preloaded_models
            ):
                log.info("model.evict_active", evicting=self._active_model,
                         loading=model_name)
                self._unload(self._active_model)
                keys_to_remove = [
                    k for k in self._models
                    if k.startswith(self._active_model + "_")
                ]
                for k in keys_to_remove:
                    self._models.pop(k, None)

            # ── Create and cache ──────────────────────────────────
            with TimedBlock(log, "model.load", model_name=model_name):
                model = self._create_model(
                    model_family=model_family,
                    model_name=model_name,
                    temperature=temperature,
                    timeout=timeout,
                    num_predict=num_predict,
                    format=format,
                )

            self._models[cache_key] = model
            self._active_model = model_name

            if preload:
                self._preloaded_models.add(model_name)

            return model

    # =====================================================
    # PRELOAD ROUTER
    # =====================================================

    def preload_router(self, model_name: str):
        if not any(k.startswith(f"ollama|{model_name}|") for k in self._models):
            log.info("model.preload_router", model_name=model_name)
            self.get_model(
                model_name=model_name,
                preload=True,
                num_predict=64,
            )

    # =====================================================
    # UNLOAD ALL
    # =====================================================

    def unload_all(self):
        log.info("model.unload_all.start")
        loaded_models = {k.split("|", 2)[1] for k in self._models.keys() if "|" in k}
        for model_name in loaded_models:
            self._unload(model_name)
        self._models.clear()
        self._active_model = None
        self._preloaded_models.clear()
        log.info("model.unload_all.done")


# =========================================================
# GLOBAL SINGLETON
# =========================================================

llm_manager = LLMManager()
