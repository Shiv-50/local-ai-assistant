import logging
import subprocess
import webbrowser
import pyperclip
import os
import pyautogui
import base64
import requests
import keyboard as kb
import re
import html
from html.parser import HTMLParser
from langchain.tools import tool
from pynput.mouse import Controller
from engine.vision import VisionEngine
from google import genai
from PIL import Image
from dotenv import load_dotenv
import trafilatura
from bs4 import BeautifulSoup
from readability import Document
from urllib.parse import urlparse
from concurrent.futures import ThreadPoolExecutor, as_completed
from ai.memory_store import search_memory

load_dotenv(
    dotenv_path=os.path.join(
        os.path.dirname(os.path.dirname(__file__)), ".env"
    )
)

# =========================================================
# APP MAP
# =========================================================

APP_MAP = {
    "chrome": "start chrome",
    "google chrome": "start chrome",
    "firefox": "start firefox",
    "edge": "start msedge",
    "microsoft edge": "start msedge",
    "brave": "start brave",
    "vscode": "code",
    "vs code": "code",
    "visual studio code": "code",
    "notepad": "notepad",
    "notepad++": "notepad++",
    "explorer": "explorer",
    "file explorer": "explorer",
    "calculator": "calc",
    "calc": "calc",
    "task manager": "taskmgr",
    "control panel": "control",
    "settings": "start ms-settings:",
    "terminal": "wt",
    "windows terminal": "wt",
    "powershell": "powershell",
    "cmd": "cmd",
    "command prompt": "cmd",
    "spotify": "start spotify",
    "vlc": "vlc",
    "paint": "mspaint",
    "word": "winword",
    "excel": "excel",
    "powerpoint": "powerpnt",
    "outlook": "outlook",
    "teams": "start teams",
    "slack": "start slack",
    "discord": "start discord",
    "zoom": "start zoom",
}

# =========================================================
# TEXT UTILITIES
# =========================================================

def clean_text(text: str) -> str:
    text = re.sub(r"\s+", " ", text)
    return text.strip()


def strip_html_tags(text: str) -> str:
    text = re.sub(r"<[^>]+>", "", text)
    text = html.unescape(text)
    text = re.sub(r"\s+", " ", text).strip()
    return text


# =========================================================
# PAGE EXTRACTION STRATEGIES
# =========================================================

def extract_with_trafilatura(raw_html: str) -> str | None:
    try:
        extracted = trafilatura.extract(
            raw_html,
            include_links=False,
            include_images=False,
            favor_precision=True,
            deduplicate=True,
        )
        if extracted and len(extracted) > 200:
            return extracted
    except Exception:
        pass
    return None


def extract_with_readability(raw_html: str) -> str | None:
    try:
        doc = Document(raw_html)
        soup = BeautifulSoup(doc.summary(), "html.parser")
        text = soup.get_text(separator=" ", strip=True)
        if len(text) > 200:
            return text
    except Exception:
        pass
    return None


def extract_basic(raw_html: str) -> str:
    soup = BeautifulSoup(raw_html, "html.parser")
    for tag in soup(["script", "style", "nav", "footer",
                     "header", "aside", "noscript", "svg", "iframe"]):
        tag.decompose()
    paragraphs = soup.find_all("p")
    return " ".join(
        p.get_text(" ", strip=True)
        for p in paragraphs
        if len(p.get_text(strip=True)) > 40
    )


# =========================================================
# CHUNK + SCORE
# =========================================================

def split_into_chunks(text: str, chunk_size: int = 1200) -> list[str]:
    return [text[i: i + chunk_size] for i in range(0, len(text), chunk_size)]


def score_chunk(chunk: str, query: str) -> int:
    query_words = query.lower().split()
    chunk_lower = chunk.lower()
    return sum(chunk_lower.count(w) for w in query_words)


def get_relevant_content(text: str, query: str, top_k: int = 3) -> str:
    chunks = split_into_chunks(text)
    scored = sorted(
        [(score_chunk(c, query), c) for c in chunks],
        reverse=True,
        key=lambda x: x[0],
    )
    selected = [c.strip() for _, c in scored[:top_k] if len(c.strip()) > 100]
    return "\n\n".join(selected)


# =========================================================
# PAGE SCRAPER
# =========================================================

def scrape_page_content(url: str, query: str = "", max_chars: int = 4000) -> str:
    headers = {
        "User-Agent": (
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
            "AppleWebKit/537.36 (KHTML, like Gecko) "
            "Chrome/120.0 Safari/537.36"
        )
    }
    try:
        response = requests.get(url, headers=headers, timeout=15, allow_redirects=True)
        if response.status_code != 200:
            return ""

        raw = response.text
        text = (
            extract_with_trafilatura(raw)
            or extract_with_readability(raw)
            or extract_basic(raw)
        )
        text = clean_text(text or "")

        if not text:
            return ""

        if query:
            text = get_relevant_content(text, query)

        if len(text) > max_chars:
            text = text[:max_chars] + "..."

        domain = urlparse(url).netloc
        return f"[Source: {domain}]\n{text}"

    except Exception as e:
        logging.debug(f"Failed scraping {url}: {e}")
        return ""


# =========================================================
# YAHOO PARSER
# =========================================================

def clean_yahoo_title(raw_title: str) -> str:
    """Strip site-name + URL breadcrumb Yahoo prepends to result titles."""
    cleaned = re.sub(r"^[A-Za-z0-9\s\.\-]+https?://[^\s]*[\s›]+", "", raw_title)
    cleaned = re.sub(r"^[A-Za-z0-9\.\-]+https?://\S+\s*", "", cleaned)
    cleaned = cleaned.lstrip("› ").strip()
    return cleaned if len(cleaned) > 3 else raw_title


def parse_yahoo_regex(html_content: str) -> list[dict]:
    import urllib.parse

    results = []
    blocks = re.split(r'class="compTitle\b', html_content)

    for b in blocks[1:]:
        url_match = re.search(r'href="([^"]+)"', b)
        if not url_match:
            continue

        url = url_match.group(1)
        match = re.search(r"/RU=([^/]+)", url)
        if match:
            url = urllib.parse.unquote(match.group(1))

        if (
            url.startswith("/")
            or "search.yahoo.com" in url
            or "bing.com/aclick" in url
            or "yahoo.com/aclick" in url
        ):
            continue

        title_match = re.search(r"<a[^>]*>([\s\S]*?)</a>", b)
        title = title_match.group(1) if title_match else ""

        desc_match = re.search(
            r'class="[^"]*(?:compText|desc)[^"]*"[^>]*>([\s\S]*?)</div>', b
        )
        desc = desc_match.group(1) if desc_match else ""
        if not desc:
            alt = re.search(r"</h3>[\s\S]*?<div[^>]*>([\s\S]*?)</div>", b)
            if alt:
                desc = alt.group(1)

        title_c = clean_yahoo_title(strip_html_tags(title))
        desc_c  = strip_html_tags(desc)

        if "Ad ·" in title_c or title_c.startswith("Ad "):
            continue

        if url and title_c and len(title_c) > 3:
            results.append({"title": title_c, "url": url, "description": desc_c})

    return results


# =========================================================
# TOOLS
# =========================================================

@tool
def search_installed_apps(query: str) -> str:
    """Searches installed applications on the Windows system using the Start Menu.
    Returns matching app names and their corresponding AppIDs.
    Use this when the exact application name or launch command is unclear."""
    logging.info(f"Searching installed apps for query: '{query}'")
    try:
        import json
        cmd = ["powershell", "-Command", "Get-StartApps | ConvertTo-Json"]
        res = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8", timeout=10)
        if res.returncode != 0:
            return f"Error executing Get-StartApps: {res.stderr}"
        if not res.stdout.strip():
            return f"No installed applications match the query '{query}'."
        
        data = json.loads(res.stdout)
        apps = []
        if isinstance(data, dict):
            apps = [data]
        elif isinstance(data, list):
            apps = data
            
        query_lower = query.lower().strip()
        matches = []
        for app in apps:
            name = app.get("Name", "")
            appid = app.get("AppID", "")
            if query_lower in name.lower() or query_lower in appid.lower():
                matches.append((name, appid))
                
        if not matches:
            return f"No installed applications match the query '{query}'."
            
        result_lines = [f"Found {len(matches)} matching applications:"]
        for name, appid in matches:
            result_lines.append(f"- Name: '{name}', AppID: '{appid}'")
        return "\n".join(result_lines)
    except Exception as e:
        return f"Failed to search installed apps: {e}"


@tool
def launch_app_by_id(appid: str) -> str:
    """Launches an application using its AppID (e.g. from search_installed_apps) via the Windows shell:AppsFolder."""
    logging.info(f"Launching app by ID: '{appid}'")
    try:
        subprocess.Popen(f'explorer.exe shell:AppsFolder\\{appid}', shell=True)
        return f"Successfully sent launch command for AppID: '{appid}'."
    except Exception as e:
        return f"Failed to launch app by AppID: {e}"


@tool
def launch_application(app_name: str) -> str:
    """Launches an application by name. Supports chrome, vscode, calculator,
    notepad, explorer, terminal, spotify, word, excel, etc. If the application
    is not in the predefined map, it will automatically search installed system apps."""
    key = app_name.lower().strip()
    cmd = APP_MAP.get(key)

    if cmd:
        logging.info(f"Launching known app '{app_name}': {cmd}")
        try:
            subprocess.Popen(cmd, shell=True)
            return f"Successfully launched {app_name}."
        except Exception as e:
            return f"Failed to launch {app_name}: {e}"
            
    # Search dynamic system applications
    logging.info(f"App '{app_name}' not in predefined map. Searching Start Apps...")
    try:
        import json
        cmd_p = ["powershell", "-Command", "Get-StartApps | ConvertTo-Json"]
        res = subprocess.run(cmd_p, capture_output=True, text=True, encoding="utf-8", timeout=10)
        if res.returncode == 0 and res.stdout.strip():
            data = json.loads(res.stdout)
            apps = [data] if isinstance(data, dict) else (data if isinstance(data, list) else [])
            matches = [app for app in apps if key in app.get("Name", "").lower() or key in app.get("AppID", "").lower()]
            
            # Look for exact or highly matching name
            exact_matches = [app for app in matches if app.get("Name", "").lower().strip() == key]
            if len(exact_matches) == 1:
                appid = exact_matches[0].get("AppID")
                subprocess.Popen(f'explorer.exe shell:AppsFolder\\{appid}', shell=True)
                return f"Successfully launched '{exact_matches[0].get('Name')}' via AppID: '{appid}'."
                
            if len(matches) == 1:
                appid = matches[0].get("AppID")
                subprocess.Popen(f'explorer.exe shell:AppsFolder\\{appid}', shell=True)
                return f"Successfully launched '{matches[0].get('Name')}' via AppID: '{appid}'."
            elif len(matches) > 1:
                matches_str = "\n".join([f"- Name: '{a.get('Name')}', AppID: '{a.get('AppID')}'" for a in matches])
                return (
                    f"Found multiple applications matching '{app_name}':\n{matches_str}\n"
                    f"Please refine your request or use launch_app_by_id directly."
                )
    except Exception as e:
        logging.warning(f"Dynamic start apps search failed: {e}")

    # Fallback to generic start
    logging.info(f"Failing back to generic start for '{app_name}'.")
    try:
        subprocess.Popen(f"start {key}", shell=True)
        return f"Attempted to launch '{app_name}' using fallback."
    except Exception as e:
        return f"Could not launch '{app_name}': {e}"


@tool
def open_url_in_browser(url: str) -> str:
    """Opens the specified URL in the system's default web browser."""
    logging.info(f"Opening URL: {url}")
    try:
        # Standardize URL
        if not (url.startswith("http://") or url.startswith("https://")):
            url = "https://" + url
        webbrowser.open(url)
        return f"Successfully opened URL: {url} in default browser."
    except Exception as e:
        return f"Failed to open URL {url}: {e}"


@tool
def focus_window(title: str) -> str:
    """Brings the window with the given title to the foreground.
    Tries exact match first, then fuzzy case-insensitive match.
    Returns a status message.
    """
    logging.info(f"Focusing window with title containing: {title}")

    try:
        import ctypes
        import re
        from ctypes import wintypes

        user32 = ctypes.WinDLL('user32', use_last_error=True)

        EnumWindows = user32.EnumWindows
        EnumWindowsProc = ctypes.WINFUNCTYPE(
            ctypes.c_bool,
            wintypes.HWND,
            wintypes.LPARAM
        )

        GetWindowTextLength = user32.GetWindowTextLengthW
        GetWindowText = user32.GetWindowTextW
        SetForegroundWindow = user32.SetForegroundWindow
        IsWindowVisible = user32.IsWindowVisible

        matches = []

        def foreach_window(hwnd, lParam):
            if not IsWindowVisible(hwnd):
                return True

            length = GetWindowTextLength(hwnd)

            if length == 0:
                return True

            buf = ctypes.create_unicode_buffer(length + 1)
            GetWindowText(hwnd, buf, length + 1)

            window_title = buf.value

            if window_title:
                if title == window_title:
                    matches.append(hwnd)
                    return False

                elif re.search(re.escape(title), window_title, re.IGNORECASE):
                    matches.append(hwnd)

            return True

        EnumWindows(EnumWindowsProc(foreach_window), 0)

        if not matches:
            return f"No window found matching title: '{title}'."

        hwnd = matches[0]

        SetForegroundWindow(hwnd)

        return f"Focused window with title containing '{title}'."

    except Exception as e:
        return f"Failed to focus window: {e}"

@tool
def mouse_click(x: int, y: int, button: str = 'left', clicks: int = 1) -> str:
    """Clicks the mouse at the specified (x, y) coordinates on the screen.
    x and y are absolute screen pixel coordinates. Button can be 'left', 'right', or 'middle'."""
    logging.info(f"Mouse click at ({x}, {y}) | button={button} | clicks={clicks}")
    try:
        width, height = pyautogui.size()
        if not (0 <= x < width and 0 <= y < height):
            return f"Error: coordinates ({x}, {y}) are out of screen bounds ({width}x{height})."
        pyautogui.click(x=x, y=y, clicks=clicks, button=button)
        return f"Successfully clicked {button} button {clicks} time(s) at ({x}, {y})."
    except Exception as e:
        return f"Failed mouse click: {e}"
  

@tool
def mouse_move(x: int, y: int) -> str:
    """Moves the mouse cursor to the specified (x, y) coordinates on the screen."""
    logging.info(f"Mouse move to ({x}, {y})")
    try:
        width, height = pyautogui.size()
        if not (0 <= x < width and 0 <= y < height):
            return f"Error: coordinates ({x}, {y}) are out of screen bounds ({width}x{height})."
        pyautogui.moveTo(x, y, duration=0.2)
        return f"Successfully moved mouse to ({x}, {y})."
    except Exception as e:
        return f"Failed to move mouse: {e}"


@tool
def press_key(key: str) -> str:
    """Presses a single keyboard key.
    Common keys include: 'win', 'enter', 'down', 'up', 'left', 'right', 'escape', 'backspace', 'tab', 'space', 'pgup', 'pgdn'."""
    logging.info(f"Pressing key: {key}")
    try:
        pyautogui.press(key)
        return f"Successfully pressed key: {key}"
    except Exception as e:
        return f"Failed to press key: {e}"


@tool
def get_mouse_position() -> str:
    """Returns the current (x, y) coordinates of the mouse cursor."""
    try:
        x, y = pyautogui.position()
        return f"Current mouse position: ({x}, {y})"
    except Exception as e:
        return f"Failed to get mouse position: {e}"


@tool
def get_screen_size() -> str:
    """Returns the screen resolution width and height in pixels."""
    try:
        width, height = pyautogui.size()
        return f"Screen resolution: {width}x{height}"
    except Exception as e:
        return f"Failed to get screen size: {e}"


@tool
def search_web(query: str) -> str:
    """Queries the web for the given query and returns detailed search results
    with page content extracted from top results."""
    headers = {
        "User-Agent": (
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
            "AppleWebKit/537.36 (KHTML, like Gecko) "
            "Chrome/120.0.0.0 Safari/537.36"
        )
    }

    results: list[dict] = []

    # Primary: Yahoo
    logging.info(f"Web search: '{query}' (Yahoo)")
    try:
        resp = requests.get(
            "https://search.yahoo.com/search",
            params={"q": query},
            headers=headers,
            timeout=12,
        )
        if resp.status_code == 200:
            results = parse_yahoo_regex(resp.text)
            if not results:
                logging.info("Yahoo returned 0 results — trying Brave fallback.")
        else:
            logging.warning(f"Yahoo status {resp.status_code} — trying Brave fallback.")
    except Exception as e:
        logging.warning(f"Yahoo failed: {e} — trying Brave fallback.")

    # Fallback: Brave
    if not results:
        logging.info(f"Web search fallback: '{query}' (Brave)")
        try:
            resp = requests.get(
                "https://search.brave.com/search",
                params={"q": query},
                headers=headers,
                timeout=12,
            )
            if resp.status_code == 200:
                brave_results: list[dict] = []
                blocks = re.split(r'<div class="snippet\b', resp.text)
                for b in blocks[1:]:
                    url_m = re.search(r'href="([^"]+)"', b)
                    if not url_m:
                        continue
                    burl = url_m.group(1)
                    if burl.startswith("/") or "search.brave.com" in burl:
                        continue

                    tm = re.search(
                        r'class="[^"]*(?:search-snippet-title|title)[^"]*"'
                        r'[^>]*title="([^"]+)"',
                        b,
                    )
                    btitle = tm.group(1) if tm else ""
                    if not btitle:
                        tm2 = re.search(
                            r'class="[^"]*(?:search-snippet-title|title)[^"]*"'
                            r"[^>]*>([\s\S]*?)</div>",
                            b,
                        )
                        btitle = tm2.group(1) if tm2 else ""

                    bdesc = ""
                    for dm in re.finditer(
                        r'class="([^"]*(?:content|snippet-description|generic-snippet)[^"]*)"'
                        r"[^>]*>([\s\S]*?)</div>",
                        b,
                    ):
                        if "result-content" not in dm.group(1) and "site-name" not in dm.group(1):
                            bdesc = dm.group(2)
                            break

                    btitle_c = strip_html_tags(btitle)
                    bdesc_c  = strip_html_tags(bdesc)

                    if (
                        "site-name" not in btitle_c
                        and "favicon" not in btitle_c
                        and burl
                        and btitle_c
                    ):
                        brave_results.append({
                            "title": btitle_c,
                            "url": burl,
                            "description": bdesc_c,
                        })

                results = brave_results
        except Exception as e:
            logging.error(f"Brave fallback failed: {e}", exc_info=True)

    if not results:
        return f"Search returned 0 results for: '{query}'"

    # Scrape top pages for richer content
    logging.info(f"Scraping top {min(3, len(results))} result pages...")
    with ThreadPoolExecutor(max_workers=5) as executor:
        future_map = {
            executor.submit(scrape_page_content, r["url"], query, 5000): r
            for r in results[:5]
        }
        for future in as_completed(future_map):
            r = future_map[future]
            try:
                content = future.result()
                if content:
                    r["page_content"] = content
            except Exception:
                pass

    formatted = []
    for idx, r in enumerate(results[:6]):
        entry = (
            f"[{idx + 1}] {r['title']}\n"
            f"    URL: {r['url']}\n"
            f"    Snippet: {r['description']}"
        )
        if r.get("page_content"):
            entry += f"\n    Detail: {r['page_content']}"
        formatted.append(entry)

    return f"Web Search Results for '{query}':\n\n" + "\n\n".join(formatted)


@tool
def execute_shell_command(command: str) -> str:
    """Executes a shell/terminal command and returns its output.
    Use for file ops, system info, scripts."""
    logging.info(f"Shell command: {command}")
    try:
        result = subprocess.run(
            command, shell=True, capture_output=True, text=True, timeout=15
        )
        output = result.stdout.strip() or result.stderr.strip()
        return output if output else "Command ran with no output."
    except subprocess.TimeoutExpired:
        return "Error: Command timed out after 15 seconds."
    except Exception as e:
        return f"Error: {e}"


@tool
def type_text(text: str, interval: float = 0.05, auto_enter: bool = False) -> str:
    """Types the given text using pyautogui.
    If auto_enter is True, presses the Enter key after typing.
    """
    logging.info(f"Typing text: {text} | interval={interval} | auto_enter={auto_enter}")
    try:
        pyautogui.write(text, interval=interval)
        if auto_enter:
            pyautogui.press('enter')
            return f"Typed text and pressed Enter."
        return "Typed text successfully."
    except Exception as e:
        return f"Failed to type text: {e}"


@tool
def press_hotkey(keys: str) -> str:
    """Presses a keyboard shortcut. E.g. ctrl+c, alt+f4, win+d, ctrl+shift+t."""
    logging.info(f"Hotkey: {keys}")
    try:
        kb.press_and_release(keys)
        return f"Pressed: {keys}"
    except Exception as e:
        return f"Failed: {e}"


@tool
def get_clipboard() -> str:
    """Returns the current text content of the system clipboard."""
    try:
        return pyperclip.paste() or "[Clipboard is empty]"
    except Exception as e:
        return f"Could not read clipboard: {e}"


@tool
def set_clipboard(text: str) -> str:
    """Copies the given text to the system clipboard."""
    try:
        pyperclip.copy(text)
        return "Copied to clipboard."
    except Exception as e:
        return f"Could not set clipboard: {e}"


# =========================================================
# VISION HELPERS
# =========================================================

def encode_image(image_path: str) -> str:
    try:
        with open(image_path, "rb") as f:
            return base64.b64encode(f.read()).decode("utf-8")
    except Exception as e:
        logging.error(f"Failed to encode image: {e}")
        return ""


# Module-level flag — persists across calls within a session
_GEMINI_RATE_LIMITED = False


def query_gemini_vision(image_path: str, prompt: str) -> str:
    global _GEMINI_RATE_LIMITED

    if _GEMINI_RATE_LIMITED:
        raise RuntimeError("Gemini rate-limited — using local fallback.")

    api_key = os.getenv("GEMINI_API_KEY")
    if not api_key:
        raise RuntimeError("GEMINI_API_KEY not set.")

    client = genai.Client(api_key=api_key)
    img = Image.open(image_path)

    try:
        logging.info("Querying Gemma for vision...")
        response = client.models.generate_content(
            model="gemma-4-31b-it",
            contents=[prompt, img],
        )
        logging.info("Gemma vision response: %s", response.text)
        return response.text
    except Exception as e:
        err = str(e).lower()
        if any(k in err for k in ("429", "resource_exhausted", "quota", "rate limit")):
            logging.warning(f"Gemini rate limit: {e}. Switching to local fallback.")
            _GEMINI_RATE_LIMITED = True
        raise
    finally:
        img.close()


def _remove_temp(path: str) -> None:
    """Silently delete a temp file."""
    try:
        if path and os.path.exists(path):
            os.remove(path)
    except Exception as e:
        logging.warning(f"Could not delete temp file {path}: {e}")


@tool
def analyze_screen_with_vision(query: str) -> str:
    """Captures a screenshot and analyses it visually using Gemini 2.5 Flash,
    falling back to local llama3.2-vision if rate-limited. Use when the user
    asks about what is currently on their screen."""
    global _GEMINI_RATE_LIMITED

    logging.info(f"analyze_screen_with_vision: '{query}'")
    img_path = None

    try:
        engine   = VisionEngine()
        img_path = engine.capture_full_screen("temp_capture.png")

        # Primary: Gemini
        if not _GEMINI_RATE_LIMITED:
            try:
                result = query_gemini_vision(img_path, query)
                logging.info("Vision: Gemini 2.5 Flash succeeded.")
                return result
            except Exception as e:
                logging.warning(f"Gemini vision failed: {e}. Falling back to llama3.2-vision.")

        # Fallback: local llama3.2-vision
        b64 = encode_image(img_path)
        if not b64:
            return "Error: Could not read captured screen image."

        payload = {
            "model":  "llama3.2-vision",
            "prompt": query,
            "images": [b64],
            "stream": False,
        }
        logging.info("Sending to llama3.2-vision...")
        resp = requests.post(
            "http://localhost:11434/api/generate",
            json=payload,
            timeout=120,
        )
        resp.raise_for_status()
        return resp.json().get("response", "")

    except Exception as e:
        logging.error(f"Vision tool failed: {e}", exc_info=True)
        return f"Error analyzing screen: {e}"

    finally:
        _remove_temp(img_path)

@tool
def retrieve_memory(query: str) -> str:
    """
    Searches semantic memory using FAISS + Gemini embeddings.
    Use when prior conversation context is required.
    """

    try:

        results = search_memory(
            query=query,
            top_k=5,
        )

        if not results:
            return "No relevant memory found."

        formatted = []

        for r in results:

            formatted.append(
                f"[{r['role']}]\n{r['content']}"
            )

        return "\n\n".join(formatted)

    except Exception as e:
        return f"Memory retrieval failed: {e}"
# =========================================================
# EXPORT
# =========================================================

ALL_TOOLS = [
    launch_application,
    search_web,
    execute_shell_command,
    type_text,
    press_hotkey,
    get_clipboard,
    set_clipboard,
    analyze_screen_with_vision,
    search_installed_apps,
    launch_app_by_id,
    mouse_click,
    mouse_move,
    press_key,
    get_mouse_position,
    get_screen_size,
    open_url_in_browser,
    focus_window,
    retrieve_memory
]