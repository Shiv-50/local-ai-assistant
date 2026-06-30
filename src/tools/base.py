# src/tools/base.py

import logging
import traceback
from functools import wraps
import json

from langchain.tools import tool
from src.utils.completion_evaluator import normalize_tool_result

def safe_tool(name: str):

    def decorator(func):

        @tool(description=name)
        @wraps(func)
        def wrapper(*args, **kwargs):

            try:

                logging.info(
                    f"[TOOL START] {func.__name__}"
                )

                result = func(*args, **kwargs)

                logging.info(
                    f"[TOOL RESULT TYPE] {type(result)}"
                )

                if isinstance(result, dict):
                    return normalize_tool_result(func.__name__, result)

                if isinstance(result, list):
                    return result

                return str(result)

            except Exception as e:

                logging.exception(
                    f"[TOOL ERROR] {func.__name__}"
                )
                
                error_trace = traceback.format_exc()

                return f"Error executing tool {func.__name__}: {str(e)}\nTraceback: {error_trace}"

        return wrapper

    return decorator