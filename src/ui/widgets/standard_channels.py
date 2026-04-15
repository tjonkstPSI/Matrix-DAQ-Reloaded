# Author: T. Onkst | Date: 03092026

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Dict, List

_PROJECT_ROOT = Path(__file__).resolve().parents[3]
_STANDARD_CHANNELS_PATH = _PROJECT_ROOT / "configs" / "standard_channels.json"

ALIAS_PATTERN = re.compile(
	r"(?:^[qcemixypvl](?:TP|PR|FL|VL|CT|PC|SP|FQ|DG|AC|DS|PW|MS|TM|TQ|PO|OT|DE|CN|HM|LA|PI|AF|VO|VS|DN)"
	r"_(?:Amb|Eng|Rad|Cac|Dyn|Cmp|Trb|Olc|Pmp|Pto|Thr|Ccs|Cat|Man|Mix|Vap|Reg|Blk|Hed|Ral|Xvr|Col|Alt"
	r"|Bat|Ign|Fan|Gen|Ldb|Bth|Epr|Ecm|Twg|Fac|Enc|Mfg|Tst|Loc|Vlv|Cyl|Fnt|Rer|Mst|Slv|Rgt|Lft|Clt|Ful"
	r"|Oil|Sld|Exh|Int|Gly|Ftr|Pan|Pdl|Spk|Trm|Air|Dew|Wet|Nag|Lpg|Phs|Cpl|Mil|Dtc|Shm|Lod|Hyd|Trn|Esp"
	r"|Emg|Std|Ssd|Flg|Fst|Bst|Pre|Pst|In|Out|Bby|Mid|Sfc|Dta|Stp|Act|Lng|Sht|Top|Bot|Nox|Oxy|Dpt|Vld"
	r"|Iso|Sae|Wat|Abs|Cnt|Cst|Gag|Avg|Roa|Ror|Lmt|[0-9]+)*$"
	r"|^[eiyx].+$)"
)


def validate_alias(alias: str) -> bool:
	"""Return True if *alias* matches the constrained naming convention."""
	return bool(ALIAS_PATTERN.match(alias))


def load_standard_channels(
	path: Path | None = None,
) -> List[Dict[str, str]]:
	"""Load the standard channel list from the local JSON file.

	Returns a list of dicts with at least ``alias`` and ``unit`` keys.
	When a server endpoint is added in the future, this function is the
	single place that needs to change.
	"""
	target = path or _STANDARD_CHANNELS_PATH
	try:
		text = target.read_text(encoding="utf-8")
		data = json.loads(text)
		channels = data.get("channels", [])
		return [
			ch for ch in channels
			if isinstance(ch, dict) and ch.get("alias", "").strip()
		]
	except Exception:
		return []
