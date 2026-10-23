"""
Optional AI second opinion (free tiers: Gemini, Groq, OpenRouter, or local Ollama).

What went wrong in v1:
  * One giant prompt with every take and an 18 s timeout. For anything longer
    than a few minutes the call timed out and the tool silently fell back to
    the weak heuristic - the launcher sent server output to /dev/null, so you
    never saw it.
  * The model returned a KEEP list: every line it forgot to list was deleted.
  * Ollama ran with its default 2-4k context, truncating long transcripts.

v2:
  * The local analysis decides; the LLM only reviews. It confirms or disputes
    local flags and may point out paraphrased retakes the text matcher can't see.
  * Anything ONLY the LLM wants removed becomes "review" (brown), never cut.
  * Overlapping windows of ~60 phrases, long timeouts, retry with backoff on
    429/503, a pause between calls for free-tier rate limits, and every failure
    is reported in the job log and the final summary.
"""
from __future__ import annotations

import json
import re
import time
import urllib.error
import urllib.request

PROVIDERS = {
    "gemini": {"label": "Google Gemini (free tier)", "model": "gemini-flash-latest", "min_interval": 6.5},
    "groq": {"label": "Groq (free tier)", "model": "openai/gpt-oss-120b", "min_interval": 2.5,
             "url": "https://api.groq.com/openai/v1/chat/completions"},
    "openrouter": {"label": "OpenRouter (free models)", "model": "meta-llama/llama-3.3-70b-instruct:free",
                   "min_interval": 4.0,
                   "url": "https://openrouter.ai/api/v1/chat/completions"},
    "ollama": {"label": "Ollama (local, offline)", "model": "qwen2.5:7b", "min_interval": 0.0,
               "url": "http://127.0.0.1:11434/api/chat"},
    "openai_compatible": {"label": "Custom OpenAI-compatible (LM Studio etc.)", "model": "", "min_interval": 0.0,
                          "url": "http://127.0.0.1:1234/v1/chat/completions"},
}

WINDOW = 60
OVERLAP = 12

SYSTEM = """You help a video editor cut a raw recording (talking head or screen tutorial).
The speaker often restarts sentences, repeats lines until they get them right, or redoes whole sections.
Each input line is one spoken phrase: ID, start time, text. Some lines carry a LOCAL flag from an
automatic matcher, e.g. [LOCAL remove 0.92 -> p0031] meaning "earlier take of p0031".

Your job:
1. For every LOCAL-flagged line: agree ("remove"), disagree ("keep") or "unsure".
2. Find other lines that should go: failed or abandoned takes that were redone later (possibly in
   different words), false starts, broken sentences, and chatter not meant for the audience
   ("is it recording?", "one second", "let me check"). Use "remove" or "unsure".

Rules:
- When something is said more than once, the LAST complete version is the keeper.
- Never remove the only version of a piece of content.
- If you are not confident, answer "unsure" - a human will check it.
- Do NOT list plain lines that should simply stay and were not flagged.
- The text may be an English translation of Hindi/Hinglish speech; judge meaning, not exact words.

Answer with JSON only:
{"items":[{"id":"p0012","verdict":"remove|keep|unsure","confidence":0.0,"reason":"max 12 words"}]}"""


class LLMError(RuntimeError):
    pass


def _http_json(url: str, payload: dict, headers: dict, timeout: float) -> dict:
    req = urllib.request.Request(url, data=json.dumps(payload).encode("utf-8"),
                                 headers=dict({"Content-Type": "application/json"}, **headers))
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return json.loads(resp.read().decode("utf-8", "replace"))


def _call_once(provider: str, model: str, api_key: str, base_url: str, user_text: str, timeout: float) -> str:
    if provider == "gemini":
        url = f"https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent"
        payload = {
            "systemInstruction": {"parts": [{"text": SYSTEM}]},
            "contents": [{"role": "user", "parts": [{"text": user_text}]}],
            "generationConfig": {"responseMimeType": "application/json", "temperature": 0.1},
        }
        data = _http_json(url, payload, {"x-goog-api-key": api_key}, timeout)
        try:
            parts = data["candidates"][0]["content"]["parts"]
            return "".join(p.get("text", "") for p in parts if not p.get("thought"))
        except (KeyError, IndexError):
            raise LLMError(f"Gemini returned no text: {json.dumps(data)[:300]}")
    if provider == "ollama":
        url = base_url or PROVIDERS["ollama"]["url"]
        payload = {"model": model, "stream": False, "format": "json",
                   "options": {"num_ctx": 16384, "temperature": 0.1},
                   "messages": [{"role": "system", "content": SYSTEM}, {"role": "user", "content": user_text}]}
        data = _http_json(url, payload, {}, timeout)
        return data.get("message", {}).get("content", "")
    url = base_url or PROVIDERS.get(provider, {}).get("url", "")
    if not url:
        raise LLMError("No endpoint URL configured for this provider.")
    headers = {"Authorization": f"Bearer {api_key}"} if api_key else {}
    if provider == "openrouter":
        headers.update({"HTTP-Referer": "http://127.0.0.1", "X-Title": "ScriptCutter v2"})
    payload = {"model": model, "temperature": 0.1,
               "messages": [{"role": "system", "content": SYSTEM}, {"role": "user", "content": user_text}]}
    if provider in ("groq", "openai_compatible"):
        payload["response_format"] = {"type": "json_object"}
    data = _http_json(url, payload, headers, timeout)
    try:
        return data["choices"][0]["message"]["content"] or ""
    except (KeyError, IndexError):
        raise LLMError(f"Unexpected response: {json.dumps(data)[:300]}")


def call(provider: str, model: str, api_key: str, base_url: str, user_text: str,
         timeout: float = 180.0, retries: int = 4, log=None) -> str:
    delay = 8.0
    for attempt in range(retries + 1):
        try:
            return _call_once(provider, model, api_key, base_url, user_text, timeout)
        except urllib.error.HTTPError as e:
            body = ""
            try:
                body = e.read().decode("utf-8", "replace")[:300]
            except Exception:
                pass
            if e.code in (429, 500, 502, 503, 504) and attempt < retries:
                wait = delay
                ra = e.headers.get("Retry-After") if e.headers else None
                if ra and ra.replace(".", "", 1).isdigit():
                    wait = max(wait, float(ra))
                m = re.search(r'"retryDelay":\s*"(\d+)', body)
                if m:
                    wait = max(wait, float(m.group(1)))
                if log:
                    log(f"AI reviewer busy (HTTP {e.code}), retrying in {wait:.0f}s...")
                time.sleep(min(wait, 90))
                delay *= 2
                continue
            if e.code in (401, 403):
                raise LLMError(f"API key rejected (HTTP {e.code}). {body}")
            if e.code == 404:
                raise LLMError(f"Model '{model}' not found for {provider} (HTTP 404). {body}")
            raise LLMError(f"HTTP {e.code}: {body}")
        except (urllib.error.URLError, TimeoutError, ConnectionError) as e:
            if attempt < retries:
                if log:
                    log(f"AI reviewer connection problem ({e}); retrying...")
                time.sleep(delay)
                delay *= 2
                continue
            raise LLMError(f"Could not reach {provider}: {e}")
    raise LLMError("AI reviewer gave up after retries.")


def parse_items(raw: str) -> list:
    text = raw.strip()
    m = re.search(r"```(?:json)?\s*(.*?)```", text, re.S)
    if m:
        text = m.group(1)
    a, b = text.find("{"), text.rfind("}")
    if a == -1 or b <= a:
        a, b = text.find("["), text.rfind("]")
        if a == -1:
            raise LLMError("Reply was not JSON")
        return json.loads(text[a:b + 1])
    data = json.loads(text[a:b + 1])
    items = data.get("items", data.get("decisions", []))
    return items if isinstance(items, list) else []


def _fmt(t: float) -> str:
    m, s = divmod(t, 60)
    return f"{int(m):02d}:{s:04.1f}"


def review(phrases: list, decisions: list, settings: dict, log=None, progress=None) -> dict:
    """Returns {"verdicts": {id: {...}}, "errors": [...], "windows": n}."""
    provider = settings.get("llm_provider", "")
    meta = PROVIDERS.get(provider)
    if not meta:
        return {"verdicts": {}, "errors": [f"Unknown AI provider '{provider}'"], "windows": 0}
    model = settings.get("llm_model") or meta["model"]
    key = settings.get("llm_api_key", "")
    base = settings.get("llm_base_url", "")
    topic = (settings.get("topic") or "").strip()
    if provider in ("gemini", "groq", "openrouter") and not key:
        return {"verdicts": {}, "errors": [f"{meta['label']} needs an API key."], "windows": 0}

    by_id = {d["id"]: d for d in decisions}
    n = len(phrases)
    windows = []
    i = 0
    while i < n:
        windows.append((i, min(n, i + WINDOW)))
        if i + WINDOW >= n:
            break
        i += WINDOW - OVERLAP
    verdicts, errors = {}, []
    last_call = 0.0
    for w_i, (a, b) in enumerate(windows):
        lines = []
        for p in phrases[a:b]:
            d = by_id.get(p["id"], {})
            flag = ""
            if d.get("confidence", 0) >= 0.45:
                flag = f" [LOCAL remove {d['confidence']:.2f}" + (f" -> {d['ref']}" if d.get("ref") else "") + "]"
            lines.append(f"{p['id']} {_fmt(p['start'])}{flag} {p['text']}")
        user = (f"Video topic: {topic}\n\n" if topic else "") + "Phrases:\n" + "\n".join(lines)
        wait = meta["min_interval"] - (time.time() - last_call)
        if wait > 0:
            time.sleep(wait)
        try:
            raw = call(provider, model, key, base, user, log=log)
            last_call = time.time()
            items = parse_items(raw)
        except (LLMError, ValueError) as e:
            last_call = time.time()
            errors.append(f"Window {w_i + 1}/{len(windows)}: {e}")
            if log:
                log(f"AI reviewer window {w_i + 1}/{len(windows)} failed: {e}")
            if "key rejected" in str(e) or "not found" in str(e):
                break
            continue
        core_a = a + (OVERLAP // 2 if w_i > 0 else 0)
        core_b = b - (OVERLAP // 2 if w_i + 1 < len(windows) else 0)
        core_ids = {p["id"] for p in phrases[core_a:core_b]}
        for it in items:
            pid = str(it.get("id", "")).strip()
            if pid not in by_id or (pid not in core_ids and pid in verdicts):
                continue
            v = str(it.get("verdict", "")).lower()
            if v not in ("remove", "keep", "unsure"):
                continue
            try:
                c = float(it.get("confidence", 0.6))
            except (TypeError, ValueError):
                c = 0.6
            verdicts[pid] = {"verdict": v, "confidence": max(0.0, min(1.0, c)),
                             "reason": str(it.get("reason", ""))[:160]}
        if progress:
            progress((w_i + 1) / len(windows))
        if log:
            log(f"AI reviewer window {w_i + 1}/{len(windows)}: {len(items)} notes")
    return {"verdicts": verdicts, "errors": errors, "windows": len(windows), "model": model, "provider": provider}


def merge(decisions: list, verdicts: dict, review_at: float = 0.45) -> int:
    """Fold LLM verdicts into local decisions. Returns how many decisions changed."""
    changed = 0
    for d in decisions:
        v = verdicts.get(d["id"])
        if not v:
            continue
        before = d["confidence"]
        note = f"AI reviewer: {v['reason']}" if v["reason"] else "AI reviewer"
        if before >= review_at:
            if v["verdict"] == "remove" and v["confidence"] >= 0.5:
                d["confidence"] = round(min(0.95, max(before, 0.7) + 0.12), 3)
                d["reason"] += " | AI agrees"
            elif v["verdict"] == "keep" and v["confidence"] >= 0.5:
                d["confidence"] = round(min(before, 0.6), 3)
                d["reason"] += f" | {note} (thinks it should stay)"
        elif v["verdict"] in ("remove", "unsure"):
            # only the AI sees a problem: flag for a human, never cut
            d["confidence"] = round(max(before, 0.5 + (0.15 * v["confidence"] if v["verdict"] == "remove" else 0)), 3)
            d["reason"] = note
            d["llm_only"] = True
        d["llm"] = v
        if d["confidence"] != before:
            changed += 1
    return changed
