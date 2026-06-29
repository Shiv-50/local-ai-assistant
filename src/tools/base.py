# src/tools/base.py

import logging
import traceback
from functools import wraps
import json

from langchain.tools import tool

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

                # Convert dict/list to json string
                if isinstance(result, (dict, list)):
                    return json.dumps(result, indent=2)
                    
                return str(result)

            except Exception as e:

                logging.exception(
                    f"[TOOL ERROR] {func.__name__}"
                )
                
                error_trace = traceback.format_exc()

                return f"Error executing tool {func.__name__}: {str(e)}\nTraceback: {error_trace}"

        return wrapper

    return decorator