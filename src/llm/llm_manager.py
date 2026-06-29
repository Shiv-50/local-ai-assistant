# src/core/llm_manager.py

import logging
import requests

from threading import Lock

from langchain_ollama import ChatOllama
from langchain_google_genai import ChatGoogleGenerativeAI
import os
from dotenv import load_dotenv

load_dotenv()

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

    OLLAMA_BASE = "http://127.0.0.1:11434"

    def __init__(self):

        self._models = {}

        self._active_model = None

        self._lock = Lock()

        self._preloaded_models = set()

    # =====================================================
    # CREATE MODEL
    # =====================================================

    def _create_model(
        self,
        model_family: str = "ollama",
        model_name: str = "qwen2.5:7b",
        temperature: float = 0.0,
        timeout: int = 45,
        num_predict: int = 1024,
        format: str = None,
    ):

        logging.info(f"Creating Ollama model: {model_name}")
        if model_family == "ollama":
            return ChatOllama(
                model=model_name,
                temperature=temperature,
                timeout=timeout,
                num_predict=num_predict,
                format=format,

                # performance
                num_ctx=4096,
                num_thread=8,
     
                # keep warm
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

    # =====================================================
    # UNLOAD MODEL
    # =====================================================

    def _unload(self, model_name: str):

        try:

            requests.post(
                f"{self.OLLAMA_BASE}/api/generate",
                json={
                    "model": model_name,
                    "keep_alive": 0
                },
                timeout=5,
            )

            logging.info(f"Unloaded model: {model_name}")

        except Exception as e:

            logging.warning(
                f"Failed unloading model {model_name}: {e}"
            )

    # =====================================================
    # GET MODEL
    # =====================================================

    def get_model(
        self,
        model_family:str = "ollama",
        model_name: str = "qwen2.5:7b",
        temperature: float = 0.0,
        timeout: int = 45,
        num_predict: int = 1024,
        preload: bool = False,
        format: str = None,
    ):

        cache_key = f"{model_name}_{temperature}_{num_predict}_{format}"

        with self._lock:

            # ---------------------------------------------
            # RETURN CACHED
            # ---------------------------------------------

            if cache_key in self._models:

                self._active_model = model_name

                return self._models[cache_key]

            # ---------------------------------------------
            # UNLOAD OLD HEAVY MODEL
            # ---------------------------------------------

            if (
                self._active_model
                and self._active_model != model_name
                and self._active_model not in self._preloaded_models
            ):

                self._unload(self._active_model)

                # Clean up cached instances of the old model
                keys_to_remove = [k for k in self._models if k.startswith(self._active_model + "_")]
                for k in keys_to_remove:
                    self._models.pop(k, None)

            # ---------------------------------------------
            # CREATE NEW MODEL
            # ---------------------------------------------

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

            # ---------------------------------------------
            # PRELOAD MARK
            # ---------------------------------------------

            if preload:
                self._preloaded_models.add(model_name)

            return model

    # =====================================================
    # PRELOAD ROUTER
    # =====================================================

    def preload_router(self, model_name: str):

        if model_name not in self._models:

            logging.info(
                f"Preloading router model: {model_name}"
            )

            self.get_model(
                model_name=model_name,
                preload=True,
                num_predict=64,
            )

    # =====================================================
    # UNLOAD ALL
    # =====================================================

    def unload_all(self):

        logging.info("Unloading all Ollama models")
        
        # Get unique base models loaded
        loaded_models = set(k.split("_")[0] for k in self._models.keys())

        for model_name in loaded_models:

            self._unload(model_name)

        self._models.clear()

        self._active_model = None

        self._preloaded_models.clear()


# =========================================================
# GLOBAL SINGLETON
# =========================================================

llm_manager = LLMManager()