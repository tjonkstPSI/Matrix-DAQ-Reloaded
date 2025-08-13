# Author: T. Onkst | Date: 08132025

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Dict, List, Tuple


def find_latest_run(runs_root: Path) -> Path | None:
    if not runs_root.exists():
        return None
    candidates: List[Tuple[float, Path]] = []
    for p in runs_root.iterdir():
        if p.is_dir():
            try:
                candidates.append((p.stat().st_mtime, p))
            except Exception:
                continue
    if not candidates:
        return None
    candidates.sort(key=lambda x: x[0])
    return candidates[-1][1]


def read_units_metadata(parquet_path: Path) -> Dict[str, str]:
    try:
        import pyarrow.parquet as pq  # type: ignore
        table = pq.read_table(parquet_path)
        md = table.schema.metadata or {}
        raw = md.get(b"units_json")
        if not raw:
            return {}
        try:
            return json.loads(raw.decode("utf-8"))
        except Exception:
            return {}
    except Exception:
        return {}


def summarize_parquet_file(parquet_path: Path) -> Dict[str, object]:
    out: Dict[str, object] = {
        "file": parquet_path.name,
        "rows": 0,
        "cols": 0,
        "columns": [],
        "time_range": (None, None),
        "time_monotonic": None,
        "units": {},
    }
    units = read_units_metadata(parquet_path)
    out["units"] = units
    try:
        import pandas as pd  # type: ignore
        df = pd.read_parquet(parquet_path)
    except Exception as e:
        out["error"] = f"failed to read: {e}"
        return out
    out["rows"] = int(len(df))
    out["cols"] = int(len(df.columns))
    out["columns"] = list(map(str, list(df.columns)))
    if "Time_Relative_s" in df.columns:
        try:
            tmin = float(df["Time_Relative_s"].min())
            tmax = float(df["Time_Relative_s"].max())
            out["time_range"] = (tmin, tmax)
            # Monotonic check (allow equal)
            ser = df["Time_Relative_s"]
            monotonic = bool(ser.is_monotonic_increasing)
            out["time_monotonic"] = monotonic
        except Exception:
            pass
    return out


def main(argv: List[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Inspect Parquet outputs from a run folder")
    parser.add_argument("--run", type=str, default=None, help="Path to a specific run folder (e.g., runs/081325_140121)")
    parser.add_argument("--show-head", type=int, default=5, help="Print first N rows of each file (0 to skip)")
    parser.add_argument("--include-chunks", action="store_true", help="Also include any remaining chunk files under seg_N folders")
    args = parser.parse_args(argv)

    project_root = Path(__file__).resolve().parents[2]
    runs_root = project_root / "runs"

    run_dir = Path(args.run).resolve() if args.run else find_latest_run(runs_root)
    if run_dir is None:
        print("[ERROR] No runs folder found.")
        return 2
    if not run_dir.exists():
        print(f"[ERROR] Run folder does not exist: {run_dir}")
        return 2

    data_dir = run_dir / "data"
    if not data_dir.exists():
        print(f"[ERROR] Data folder missing in run: {data_dir}")
        return 2

    files: List[Path] = []
    # Prefer finalized files (data.parquet or data_*.parquet)
    files.extend(sorted(data_dir.glob("data.parquet")))
    files.extend(sorted(data_dir.glob("data_*.parquet")))

    if not files and args.include_chunks:
        # Fallback to any chunk files left in seg_N
        for seg in sorted(data_dir.glob("seg_*")):
            files.extend(sorted(seg.glob("*.parquet")))

    if not files:
        print(f"[WARN] No Parquet files found in {data_dir}")
        return 0

    print(f"Run: {run_dir}")
    print("Files:")
    for f in files:
        print(f"  - {f.name}")

    union_cols: Dict[str, int] = {}
    for f in files:
        summary = summarize_parquet_file(f)
        if "error" in summary:
            print(f"\n{f.name}: ERROR {summary['error']}")
            continue
        print(
            f"\n{summary['file']}: rows={summary['rows']} cols={summary['cols']} time_range={summary['time_range']} monotonic={summary['time_monotonic']}"
        )
        cols = summary.get("columns", [])
        for c in cols:
            union_cols[c] = union_cols.get(c, 0) + 1
        units = summary.get("units", {}) or {}
        if units:
            # Show a small sample of units mappings
            sample_items = list(units.items())[:8]
            print("units_sample:", dict(sample_items))
        if args.show_head > 0:
            try:
                import pandas as pd  # type: ignore
                df = pd.read_parquet(f)
                print(df.head(args.show_head).to_string(index=False))
            except Exception as e:
                print(f"[WARN] failed to show head for {f.name}: {e}")

    # Union columns across files
    if union_cols:
        ordered = ["Time_Relative_s", "Time_Absolute_iso8601"]
        others = [c for c in union_cols.keys() if c not in ordered]
        ordered.extend(sorted(others))
        print("\nUnion of columns across files (ordered):")
        print(", ".join(ordered[:64]) + (" ..." if len(ordered) > 64 else ""))

    return 0


if __name__ == "__main__":
    sys.exit(main())


