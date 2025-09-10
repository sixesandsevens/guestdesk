#!/usr/bin/env python3
"""
Compute a suggested global DX/DY from current vs target positions.

Usage:
  python3 scripts/solve_global_offset.py P curX curY tgtX tgtY [curX curY tgtX tgtY]...

Where:
  - P is the baseline pad used by single-line fields (e.g., 2 or 3)
  - For single-line fields: curY is the box's y (not baseline), targetY is the printed rule y
    The script accounts for baseline pad: dy = targetY - (curY + P)
  - For X you can pass current x and a desired target x if you want a lateral adjustment; otherwise
    pass the same value to indicate no change (dx=0) for that anchor.

Example:
  # phone=(211,630) should sit on baseline 642; email=(392,630)->642; name=(151,622)->634, pad=2
  python3 scripts/solve_global_offset.py 2 \
    211 630  211 642 \
    392 630  392 642 \
    151 622  151 634

Outputs:
  SUGGESTED_GLOBAL_DX=...
  SUGGESTED_GLOBAL_DY=...
"""

import sys
import statistics as stats

def main(argv):
    # Expect argv = [P, curX, curY, tgtX, tgtY, ...] where count = 1 + 4*n
    if len(argv) < 5 or ((len(argv) - 1) % 4) != 0:
        raise SystemExit(
            "usage: solve_global_offset.py P curX curY tgtX tgtY [curX curY tgtX tgtY]..."
        )
    try:
        P = float(argv[0])
    except Exception:
        raise SystemExit("First arg P must be numeric (baseline pad)")
    vals = list(map(float, argv[1:]))
    dxs, dys = [], []
    for i in range(0, len(vals), 4):
        curX, curY, tgtX, tgtY = vals[i:i+4]
        dxs.append(tgtX - curX)
        dys.append(tgtY - (curY + P))  # baseline adjustment for single-line
    DX = stats.median(dxs)
    DY = stats.median(dys)
    print(f"SUGGESTED_GLOBAL_DX={DX:.1f}")
    print(f"SUGGESTED_GLOBAL_DY={DY:.1f}")

if __name__ == "__main__":
    main(sys.argv[1:])
