from __future__ import annotations

import re
from collections.abc import Mapping, Sequence


class SecretPersistenceError(ValueError):
    """Raised when a value looks unsafe to persist in the local catalog."""


_SECRET_KEY_RE = re.compile(
    r"(?i)(password|passwd|pwd|secret|token|api[_-]?key|authorization|credential|"
    r"private[_-]?key|cookie|set[_-]?cookie|sap[_-]?sessionid|__vcap_id__|mysapsso2|"
    r"jsessionid)"
)
_SECRET_TEXT_RE = re.compile(
    r"(?i)(authorization\s*[:=]|bearer\s+\S+|"
    r"(?:password|passwd|pwd|secret|token|api[_-]?key|credential|cookie|set-cookie)"
    r"\s*[:=]|"
    r"(?:bw[_\-\s]*cookie[_\-\s]*file|cookies?[_\-\s]*(?:file|path|jar))"
    r"\s*[:=]|"
    r"(?:SAP_SESSIONID(?:_[A-Za-z0-9]+)*|__VCAP_ID__|MYSAPSSO2|JSESSIONID)\s*=|"
    r"(?:https?|ftp)://[^\s/:@]+:[^\s/@]+@)"
)


def assert_no_persisted_secrets(value: object, *, path: str = "value") -> None:
    """Reject obvious credential-bearing structures before SQLite writes.

    The catalog is intentionally metadata-only. This guard is conservative for key names and
    common inline credential syntaxes while still allowing ordinary BW object names and text.
    """

    if isinstance(value, Mapping):
        for raw_key, item in value.items():
            key = str(raw_key)
            child_path = f"{path}.{key}"
            if _SECRET_KEY_RE.search(key):
                raise SecretPersistenceError(f"refusing to persist secret-like field under {path}")
            assert_no_persisted_secrets(item, path=child_path)
        return

    if isinstance(value, str):
        if _SECRET_TEXT_RE.search(value):
            raise SecretPersistenceError(f"refusing to persist secret-like text at {path}")
        return

    if isinstance(value, Sequence) and not isinstance(value, bytes | bytearray):
        for index, item in enumerate(value):
            assert_no_persisted_secrets(item, path=f"{path}[{index}]")
