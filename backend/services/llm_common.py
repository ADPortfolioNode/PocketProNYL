"""Shared helpers for the PocketProNYL language-model provider chain.

Ported from the funkStudio LLM chain (PRs #16-#20), adapted to NYL:

* a fixed provider order, not a latency order;
* per-provider errors capped at 300 chars, the *joined* chain error capped
  at 1000 chars -- a single Gemini ResourceExhausted error alone is 301
  chars, so a 300-char joined cap cut the next rung's note off entirely
  (funkStudio PR #18);
* API keys are stripped from every surfaced error (replaced with [key]);
* hand-rolled HTTP calls use a real User-Agent. Cloudflare in front of
  Groq rejects the stdlib urllib default (Python-urllib/3.x) with
  403 error code 1010 (funkStudio PR #19); the same applies to any
  hand-rolled client here, so Groq/Grok/Ollama all send PocketPro/1.0.
"""

USER_AGENT = "PocketPro/1.0"

# Per-provider error detail cap (each rung's error is truncated to this).
PER_PROVIDER_ERROR_CAP = 300

# Joined chain error cap. Must exceed PER_PROVIDER_ERROR_CAP so that a
# long first-rung error cannot push later rungs' notes out of the string.
CHAIN_ERROR_CAP = 1000

KEY_REPLACEMENT = "[key]"


def strip_api_keys(text, keys):
    """Replace every non-empty key value in *text* with [key].

    Keys are matched longest-first so a key that contains another key
    cannot leave a partial value behind. Non-string input is returned
    unchanged (callers pass it through str() first when needed).
    """
    if not isinstance(text, str):
        return text
    for key in sorted((k for k in keys if k), key=len, reverse=True):
        text = text.replace(key, KEY_REPLACEMENT)
    return text


def sanitize_error(text, keys):
    """Strip keys from an error string and cap it to one rung's budget."""
    if text is None:
        return ""
    return strip_api_keys(str(text), keys)[:PER_PROVIDER_ERROR_CAP]


def join_chain_errors(notes):
    """Join per-provider ``"<provider>: <detail>"`` notes, capped at 1000.

    Empty notes are dropped. The cap is applied to the joined string, so
    every rung's note survives unless the total genuinely exceeds 1000
    chars (with each rung already capped at 300, at most four rungs fit
    in full -- the chain has five, so the tail rung may still truncate
    in the pathological all-long-errors case, but a single long Gemini
    error can no longer erase the Groq note that follows it).
    """
    joined = "; ".join(note for note in notes if note)
    return joined[:CHAIN_ERROR_CAP]


def parse_sentinel(text, prefix):
    """Split a ``__LM_UNAVAILABLE__:<kind>:<detail>`` sentinel.

    Returns ``(kind, detail)`` or ``(None, None)`` when *text* is not a
    sentinel. The detail may itself contain colons, so only the first
    two segments are split off.
    """
    if not isinstance(text, str) or not text.startswith(prefix):
        return None, None
    rest = text[len(prefix):].lstrip(":")
    kind, _, detail = rest.partition(":")
    return kind or "api_error", detail
