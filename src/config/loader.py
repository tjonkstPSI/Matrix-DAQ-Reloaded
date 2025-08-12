# Author: T. Onkst | Date: 08122025

from __future__ import annotations

from pathlib import Path
from typing import Any, Dict, Optional

try:
    import yaml  # type: ignore
except Exception:  # pragma: no cover - dependency not installed yet
    yaml = None  # placeholder; caller should handle None

try:
    from jsonschema import Draft202012Validator  # type: ignore
except Exception:  # pragma: no cover
    Draft202012Validator = None


def load_yaml_config(config_path: Path) -> Dict[str, Any]:
    """
    Load a YAML configuration file if present. Returns an empty dict when
    the file does not exist or when PyYAML is unavailable.
    """
    if not config_path.exists() or yaml is None:
        return {}
    text = config_path.read_text(encoding="utf-8")
    data = yaml.safe_load(text) or {}
    if not isinstance(data, dict):
        raise ValueError(f"Config root must be a mapping: {config_path}")
    return data


def validate_with_schema(config: Dict[str, Any], schema_path: Path) -> None:
    if Draft202012Validator is None:
        return
    if not schema_path.exists():
        return
    schema = yaml.safe_load(schema_path.read_text(encoding="utf-8")) if schema_path.suffix in {".yaml", ".yml"} else None
    if schema is None and schema_path.suffix == ".json":
        import json
        schema = json.loads(schema_path.read_text(encoding="utf-8"))
    if not schema:
        return
    validator = Draft202012Validator(schema)
    errors = sorted(validator.iter_errors(config), key=lambda e: e.path)
    if errors:
        msgs = [f"{list(e.path)}: {e.message}" for e in errors]
        raise ValueError("Schema validation failed: " + "; ".join(msgs))


