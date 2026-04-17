# Author: T. Onkst | Date: 03092026

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Dict, List, Optional

_PROJECT_ROOT = Path(__file__).resolve().parents[3]
_SCALE_LIBRARY_PATH = _PROJECT_ROOT / "configs" / "scale_library.json"


def load_scale_library(path: Optional[Path] = None) -> List[Dict[str, Any]]:
	"""Load the scale library from the local JSON file.

	Returns a list of scale dicts. Each entry must have a non-empty ``name``
	and a ``type`` of ``linear`` or ``table``; malformed entries are skipped.

	When a server endpoint is added later, this function is the single
	place that needs to change (e.g., HTTP GET with JSON cache fallback).
	"""
	target = path or _SCALE_LIBRARY_PATH
	try:
		text = target.read_text(encoding="utf-8")
		data = json.loads(text)
		scales = data.get("scales", []) if isinstance(data, dict) else []
	except Exception:
		return []

	result: List[Dict[str, Any]] = []
	for s in scales:
		if not isinstance(s, dict):
			continue
		name = str(s.get("name", "")).strip()
		stype = str(s.get("type", "")).strip().lower()
		if not name or stype not in ("linear", "table"):
			continue
		result.append(s)
	return result
