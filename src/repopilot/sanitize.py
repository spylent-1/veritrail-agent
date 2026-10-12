"""Keep known credentials and terminal control sequences out of displayed/recorded text."""

import re
from typing import Any

from repopilot.config import api_key

CONTROL = re.compile(
    r"\x1b\][^\x07]*(?:\x07|\x1b\\)|\x1b\[[0-?]*[ -/]*[@-~]|[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]"
)


def clean(text: str) -> str:
    value = CONTROL.sub("", text)
    secret = api_key()
    return value.replace(secret, "[REDACTED]") if secret else value


def clean_data(value: Any) -> Any:
    """Sanitize strings before JSON escaping, including nested tool output."""
    if isinstance(value, str):
        return clean(value)
    if isinstance(value, dict):
        return {key: clean_data(item) for key, item in value.items()}
    if isinstance(value, list):
        return [clean_data(item) for item in value]
    return value
