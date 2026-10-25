"""Best-effort credential redaction for exported handoffs."""

import re

_SECRET_NAME = r"(?:api[_-]?key|client[_-]?secret|password|passwd|secret|token|access[_-]?token|refresh[_-]?token|access[_-]?key|secret[_-]?access[_-]?key|authorization|cookie|private[_-]?key|database[_-]?url|connection[_-]?string)"

_PATTERNS = [
    (
        re.compile(
            r"-----BEGIN (?:[A-Z]+ )?PRIVATE KEY-----.*?-----END (?:[A-Z]+ )?PRIVATE KEY-----", re.DOTALL
        ),
        "[REDACTED PRIVATE KEY]",
    ),
    (
        re.compile(
            r"\b(?:gh[pousr]_[A-Za-z0-9]{20,}|github_pat_[A-Za-z0-9_]{20,}|sk-(?:ant-)?[A-Za-z0-9_-]{20,}|(?:AKIA|ASIA)[A-Z0-9]{16})\b"
        ),
        "[REDACTED CREDENTIAL]",
    ),
    (re.compile(r"\beyJ[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+\b"), "[REDACTED JWT]"),
    (re.compile(r"(?i)(\bbearer\s+)[A-Za-z0-9._~+/-]+=*"), r"\1[REDACTED]"),
    (re.compile(r"(?im)(\b(?:authorization|proxy-authorization)\s*:\s*)[^\r\n]+"), r"\1[REDACTED]"),
    (re.compile(r"(?im)(\b(?:cookie|set-cookie)\s*:\s*)[^\r\n]+"), r"\1[REDACTED]"),
    (re.compile(r"([A-Za-z][A-Za-z0-9+.-]*://)[^/\s@]+@"), r"\1[REDACTED]@"),
    (
        re.compile(rf"(?is)(\b{_SECRET_NAME}[\"']?\s*[:=]\s*)([\"'])(?:\\.|(?!\2).)*(?:\2|$)"),
        r"\1\2[REDACTED]\2",
    ),
    (
        re.compile(
            rf"""(?i)(\b{_SECRET_NAME}["']?\s*[:=]\s*["']?)[^\s"'`,;]+"""
        ),
        r"\1[REDACTED]",
    ),
]


def redact_text(text: str) -> str:
    for pattern, replacement in _PATTERNS:
        text = pattern.sub(replacement, text)
    # Session titles and tool output can contain terminal escape sequences.
    return re.sub(r"[\x00-\x08\x0b-\x1f\x7f-\x9f\u202a-\u202e\u2066-\u2069]",
                  lambda match: f"\\u{ord(match.group()):04x}", text)


def redact_payload(value):
    if isinstance(value, str):
        return redact_text(value)
    if isinstance(value, list):
        return [redact_payload(item) for item in value]
    if isinstance(value, dict):
        secret_keys = {
            "apikey",
            "clientsecret",
            "password",
            "secret",
            "token",
            "accesstoken",
            "refreshtoken",
            "secretaccesskey",
            "authorization",
            "proxyauthorization",
            "cookie",
            "setcookie",
            "privatekey",
            "databaseurl",
            "connectionstring",
        }
        return {
            key: "[REDACTED]"
            if re.sub(r"[^a-z0-9]", "", str(key).lower()) in secret_keys
            else redact_payload(item)
            for key, item in value.items()
        }
    return value
