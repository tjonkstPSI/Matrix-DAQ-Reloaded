# Author: T. Onkst | Date: 08182025

from __future__ import annotations

import argparse
import sys
import time
from typing import Any, Sequence


def _shape_of(samples: Any) -> str:
    try:
        import numpy as np  # type: ignore
        if isinstance(samples, np.ndarray):
            return f"np.ndarray{samples.shape}"
    except Exception:
        pass
    if isinstance(samples, list):
        if samples and isinstance(samples[0], list):
            return f"list[{len(samples)}x{len(samples[0])}]"
        return f"list[{len(samples)}]"
    return type(samples).__name__


def _mean(seq: Sequence[float]) -> float:
    try:
        return sum(seq) / float(len(seq) or 1)
    except Exception:
        return 0.0


def run_check(phys: str, rate: float, n: int, timeout: float, samps_per_chan: int, vmin: float, vmax: float, loops: int) -> int:
    try:
        from nidaqmx import Task  # type: ignore
        from nidaqmx.constants import AcquisitionType  # type: ignore
    except Exception as e:  # pragma: no cover
        print(f"[ERROR] NI-DAQmx Python not available: {e}")
        return 2

    print("[INFO] Manual read check:")
    print(f"  phys={phys} rate={rate}Hz n={n} timeout={timeout}s samps_per_chan={samps_per_chan} range=[{vmin},{vmax}] loops={loops}")

    t = Task()
    try:
        t.ai_channels.add_ai_voltage_chan(phys, min_val=float(vmin), max_val=float(vmax))
        t.timing.cfg_samp_clk_timing(rate=float(rate), sample_mode=AcquisitionType.CONTINUOUS, samps_per_chan=int(samps_per_chan))
        t.start()
        print("[INFO] Task started")
        ok_reads = 0
        for i in range(max(1, loops)):
            t0 = time.time()
            try:
                samples = t.read(number_of_samples_per_channel=int(n), timeout=float(timeout))
                dt = (time.time() - t0) * 1000.0
                shp = _shape_of(samples)
                if isinstance(samples, list) and samples and isinstance(samples[0], list):
                    means = [_mean(ch) for ch in samples]
                    preview = ", ".join(f"{m:.3f}" for m in means[:4])
                elif isinstance(samples, list):
                    preview = f"{_mean(samples):.3f}"
                else:
                    # Single scalar or unknown container
                    try:
                        preview = f"{float(samples):.3f}"
                    except Exception:
                        preview = str(samples)
                print(f"[READ {i:04d}] {dt:.1f} ms shape={shp} preview={preview}")
                ok_reads += 1
            except Exception as e:
                dt = (time.time() - t0) * 1000.0
                print(f"[READ {i:04d}] {dt:.1f} ms ERROR: {e}")
                # brief pause to avoid tight error loop
                time.sleep(0.05)
        print(f"[INFO] Completed with {ok_reads}/{loops} successful reads")
        return 0
    finally:
        try:
            t.close()
        except Exception:
            pass


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description="Manual NI-DAQmx read check for a single AI voltage channel")
    p.add_argument("--phys", required=True, help="Physical channel path, e.g., MATRIXMod3/ai0")
    p.add_argument("--rate", type=float, default=200.0, help="Sample clock rate (Hz) for the task")
    p.add_argument("--n", type=int, default=10, help="Samples per channel per read")
    p.add_argument("--timeout", type=float, default=1.0, help="Read timeout (s)")
    p.add_argument("--samps-per-chan", type=int, default=400, help="DAQmx samps_per_chan (buffer) configured on the task")
    p.add_argument("--vmin", type=float, default=-10.0, help="Channel min range")
    p.add_argument("--vmax", type=float, default=10.0, help="Channel max range")
    p.add_argument("--loops", type=int, default=60, help="How many reads to perform")
    args = p.parse_args(argv)

    return run_check(
        phys=str(args.phys),
        rate=float(args.rate),
        n=int(args.n),
        timeout=float(args.timeout),
        samps_per_chan=int(args.samps_per_chan),
        vmin=float(args.vmin),
        vmax=float(args.vmax),
        loops=int(args.loops),
    )


if __name__ == "__main__":
    sys.exit(main())

from nidaqmx import Task
from nidaqmx.constants import AcquisitionType
t = Task()
t.ai_channels.add_ai_voltage_chan("AGENTMod1/ai0", min_val=0.0, max_val=10.0)
t.timing.cfg_samp_clk_timing(rate=100.0, sample_mode=AcquisitionType.CONTINUOUS, samps_per_chan=100)
t.start()
print("started")
data = t.read(number_of_samples_per_channel=10, timeout=0.2)  # 0.2s to give headroom
print("read ok, samples:", len(data) if isinstance(data, list) else "1-chan")
t.close()