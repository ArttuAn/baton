"""Best-effort credential redaction for exported handoffs."""

import re
import unicodedata

_SECRET_NAME = r"(?:api[_-]?key|client[_-]?secret|password|passwd|secret|token|access[_-]?token|refresh[_-]?token|access[_-]?key|secret[_-]?access[_-]?key|authorization|cookie|private[_-]?key|database[_-]?url|connection[_-]?string)"
_SECRET_KEY = re.compile(_SECRET_NAME)
_UNICODE_ESCAPE = re.compile(r"\\u([0-9a-fA-F]{4})")
_UNICODE32_ESCAPE = re.compile(r"\\U([0-9a-fA-F]{8})")
_HTML_ENTITY = re.compile(r"&#(?:x([0-9a-fA-F]+)|(\d+));")
_PERCENT_ESCAPE = {"%3A": ":", "%3D": "=", "%20": " ", "%22": '"'}
# Latin-looking Cyrillic letters that NFKC alone does not fold to ASCII.
_CONFUSABLES = str.maketrans(
    {"а": "a", "е": "e", "о": "o", "р": "p", "с": "c", "х": "x", "у": "y", "і": "i", "ѕ": "s", "һ": "h"}
)


def _key_name(key) -> str:
    normalized = unicodedata.normalize("NFKC", str(key).lower()).translate(_CONFUSABLES)
    return re.sub(r"[^a-z0-9]", "", normalized)

_PATTERNS = [
    (
        re.compile(
            r"-----BEGIN [A-Z0-9 ]*PRIVATE KEY(?: BLOCK)?-----.*?-----END [A-Z0-9 ]*PRIVATE KEY(?: BLOCK)?-----",
            re.DOTALL,
        ),
        "[REDACTED PRIVATE KEY]",
    ),
    (
        re.compile(
            r"\b(?:gh[pousr]_[A-Za-z0-9]{20,}|github_pat_[A-Za-z0-9_]{20,}|sk-(?:ant-)?[A-Za-z0-9_-]{20,}|sk_(?:live|test)_[A-Za-z0-9]{16,}|rk_live_[A-Za-z0-9]{16,}|glpat-[A-Za-z0-9_-]{16,}|(?:AKIA|ASIA)[A-Z0-9]{16})\b"
        ),
        "[REDACTED CREDENTIAL]",
    ),
    (re.compile(r"\beyJ[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+\b"), "[REDACTED JWT]"),
    (re.compile(r"(?i)\bbasic\s+[A-Za-z0-9+/=]{8,}"), "[REDACTED]"),
    (re.compile(r"(?i)(\bbearer\s+)[A-Za-z0-9._~+/-]+=*"), r"\1[REDACTED]"),
    (re.compile(r"(?im)(\b(?:authorization|proxy-authorization)\s*:\s*)[^\r\n]+"), r"\1[REDACTED]"),
    (re.compile(r"(?im)(\b(?:cookie|set-cookie)\s*:\s*)[^\r\n]+"), r"\1[REDACTED]"),
    (re.compile(r"([A-Za-z][A-Za-z0-9+.-]*://)[^/\s@]+@"), r"\1[REDACTED]@"),
    (
        re.compile(rf"(?im)^(\s*{_SECRET_NAME}\s*:\s*[|>][+-]?[^\n]*\n)((?:[ \t]+[^\n]*\n?)+)"),
        r"\1  [REDACTED BLOCK]\n",
    ),
    (
        re.compile(rf"(?i)(\b{_SECRET_NAME}[\"']?\s*[:=]\s*)([\[{{])[^\]\}}]*[\]\}}]"),
        r"\1[REDACTED]",
    ),
    (
        re.compile(rf"(?is)(\b{_SECRET_NAME}[\"']?\s*[:=]\s*)([\"'])(?:\\.|(?!\2).)*(?:\2|$)"),
        r"\1\2[REDACTED]\2",
    ),
    (
        re.compile(rf"""(?i)(\b{_SECRET_NAME}["']?\s*[:=]\s*["']?)(?![\{{\[])[^\s"'`,;|>}}\]]+"""),
        r"\1[REDACTED]",
    ),
    (
        re.compile(rf"(?i)(\b{_SECRET_NAME}[\"']?\s+)(?![\{{\[])([^\s\"'`,;|>}}\]]+)"),
        r"\1[REDACTED]",
    ),
]


def _decoded_char(codepoint: int, original: str) -> str:
    # Lone surrogates are not valid UTF-8 and would abort an export.
    if 0xD800 <= codepoint <= 0xDFFF or codepoint > 0x10FFFF:
        return original
    return chr(codepoint)


def _decode_escape(match: re.Match) -> str:
    return _decoded_char(int(match.group(1), 16), match.group(0))


def _decode_entity(match: re.Match) -> str:
    digits = match.group(1) or match.group(2)
    return _decoded_char(int(digits, 16 if match.group(1) else 10), match.group(0))


def redact_text(text: str) -> str:
    # Tool output carries several encodings of the same assignment. Decode the
    # harmless ones before matching so a key or value cannot hide behind them.
    text = _UNICODE_ESCAPE.sub(_decode_escape, text)
    text = _UNICODE32_ESCAPE.sub(_decode_escape, text)
    text = _HTML_ENTITY.sub(_decode_entity, text)
    text = re.sub(r"%3[aAdD]|%20|%22", lambda m: _PERCENT_ESCAPE[m.group(0).upper()], text)
    # Escaped quotes hide assignments inside JSON strings for the same reason.
    text = re.sub(r'\\(["/])', r"\1", text)
    for pattern, replacement in _PATTERNS:
        text = pattern.sub(replacement, text)
    return safe_text(text)


_TERMINAL_CONTROL = re.compile(
    r"[\x00-\x08\x0b-\x1f\x7f-\x9f\u202a-\u202e\u2066-\u2069\ud800-\udfff]"
)


def safe_text(value) -> str:
    """Render terminal controls inert before printing untrusted text."""
    return _TERMINAL_CONTROL.sub(lambda match: f"\\u{ord(match.group()):04x}", str(value))


def redact_payload(value):
    if isinstance(value, str):
        return redact_text(value)
    if isinstance(value, list):
        return [redact_payload(item) for item in value]
    if isinstance(value, dict):
        # Use the same secret-name vocabulary as text redaction, so keys such as
        # x-api-key, auth-token or passwd cannot slip through a hand-maintained list.
        return {
            key: "[REDACTED]"
            if _SECRET_KEY.search(_key_name(key))
            else redact_payload(item)
            for key, item in value.items()
        }
    return value
