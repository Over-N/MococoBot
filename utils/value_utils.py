from typing import Any


def to_bool_out(value: Any) -> bool:
    return str(value).strip().lower() in {"1", "true", "yes", "on"}
