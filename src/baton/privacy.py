"""Best-effort credential redaction for exported handoffs."""

import re

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
    (
        re.compile(
            r"""(?i)(\b(?:api[_-]?key|client[_-]?secret|password|secret|token|access[_-]?key|secret[_-]?access[_-]?key)["']?\s*[:=]\s*["']?)[^\s"'`,;]+"""
        ),
        r"\1[REDACTED]",
    ),
]


def redact_text(text: str) -> str:
    for pattern, replacement in _PATTERNS:
        text = pattern.sub(replacement, text)
    return text


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
        }
        return {
            key: "[REDACTED]"
            if re.sub(r"[^a-z0-9]", "", str(key).lower()) in secret_keys
            else redact_payload(item)
            for key, item in value.items()
        }
    return value
