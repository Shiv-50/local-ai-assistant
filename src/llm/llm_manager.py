# src/llm/llm_manager.py — replace the whole file body with:

import requests
from threading import Lock

from langchain_ollama import ChatOllama
import os
from dotenv import load_dotenv

from src.utils.logger import get_logger, TimedBlock
from src.utils.timeout import TIMEOUTS

load_dotenv()

log = get_logger(__name__)


class LLMManager:
    """
    Local-only Ollama model manager (target architecture: all inference
    stays on-box, models <=8B params). Cloud fallback intentionally
    removed -- if you need it back, gate it behind an env var, don't
    leave it live.
    """

    OLLAMA_BASE = os.getenv("OLLAMA_BASE", "http://127.0.0.1:11434")

    def __init__(self):
        # cache_key -> (model_name, model_instance)
        self._models: dict[str, tuple[str, object]] = {}
        self._active_model: str | None = None
        self._lock = Lock()
        self._preloaded_models: set[str] = set()

    def _create_model(
        self,
        model_name: str = "qwen2.5:7b",
        temperature: float = 0.0,
        timeout: int = TIMEOUTS.LLM_INFERENCE,
        num_predict: int = 1024,
        format: str = None,
    ):
        log.info("model.create", model_name=model_name,
                 temperature=temperature, timeout=timeout, num_predict=num_predict)

        return ChatOllama(
            model=model_name,
            temperature=temperature,
            timeout=timeout,
            num_predict=num_predict,
            format=format,
            num_ctx=8192,
            num_thread=8,
            keep_alive="20m",
        )

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

    def get_model(
        self,
        model_name: str = "qwen2.5:7b",
        temperature: float = 0.0,
        timeout: int = TIMEOUTS.LLM_INFERENCE,
        num_predict: int = 1024,
        preload: bool = False,
        format: str = None,
    ):
        cache_key = f"{model_name}_{temperature}_{num_predict}_{format}"

        with self._lock:
            if cache_key in self._models:
                log.debug("model.cache_hit", model_name=model_name)
                self._active_model = model_name
                return self._models[cache_key][1]

            if (
                self._active_model
                and self._active_model != model_name
                and self._active_model not in self._preloaded_models
            ):
                log.info("model.evict_active", evicting=self._active_model,
                         loading=model_name)
                self._unload(self._active_model)
                keys_to_remove = [
                    k for k, (mn, _) in self._models.items()
                    if mn == self._active_model
                ]
                for k in keys_to_remove:
                    self._models.pop(k, None)

            with TimedBlock(log, "model.load", model_name=model_name):
                model = self._create_model(
                    model_name=model_name,
                    temperature=temperature,
                    timeout=timeout,
                    num_predict=num_predict,
                    format=format,
                )

            self._models[cache_key] = (model_name, model)
            self._active_model = model_name

            if preload:
                self._preloaded_models.add(model_name)

            return model

    def preload_router(self, model_name: str):
        if model_name in self._preloaded_models:
            return
        log.info("model.preload_router", model_name=model_name)
        self.get_model(
            model_name=model_name,
            preload=True,
            num_predict=64,
        )

    def unload_all(self):
        log.info("model.unload_all.start")
        loaded_models = {mn for mn, _ in self._models.values()}
        for model_name in loaded_models:
            self._unload(model_name)
        self._models.clear()
        self._active_model = None
        self._preloaded_models.clear()
        log.info("model.unload_all.done")


llm_manager = LLMManager()