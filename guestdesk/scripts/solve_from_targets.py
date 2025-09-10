#!/usr/bin/env python3
"""
Solve global DX/DY and per-field residual nudges from a target baseline table.

Reads:
  - Boxes JSON:   /opt/guestdesk/guestdesk/utils/grievance_boxes.json
  - Targets JSON: /opt/guestdesk/guestdesk/utils/grievance_targets.json

Assumes single-line fields are bottom-aligned and use baseline pad P
  (from env GRV_BASELINE_PAD or GRIEVANCE_BASELINE_PAD, default 3).

Outputs suggested GLOBAL_DX/DY (median), and per-field residual dy after
applying the global shift so you can micro-trim the few that need it.
"""
import json, os, statistics as stats

BOXES_PATH = "/opt/guestdesk/guestdesk/utils/grievance_boxes.json"
TARGETS_PATH = "/opt/guestdesk/guestdesk/utils/grievance_targets.json"

def main():
    with open(BOXES_PATH) as f:
        boxes = json.load(f)
    with open(TARGETS_PATH) as f:
        targets = json.load(f)

    pad = float(os.environ.get("GRV_BASELINE_PAD", os.environ.get("GRIEVANCE_BASELINE_PAD", "3")))

    pairs = []
    for key, tgt_y in targets.items():
        if key not in boxes: 
            continue
        x, y, w, h = boxes[key]
        cur_baseline = y + pad
        dy = tgt_y - cur_baseline
        pairs.append((key, x, y, tgt_y, dy))

    if not pairs:
        print("No matching targets found.")
        return 1

    dvals = [dy for (_, _, _, _, dy) in pairs]
    dy_med = stats.median(dvals)
    dx_med = 0.0  # only solve Y globally by default

    print(f"SUGGESTED_GLOBAL_DX={dx_med:.1f}")
    print(f"SUGGESTED_GLOBAL_DY={dy_med:.1f}")
    print()
    print("Residual per-field (apply after baking global):")
    for key, x, y, tgt_y, dy in pairs:
        resid = dy - dy_med
        if abs(resid) < 0.1:
            continue
        print(f"  {key}: dy={resid:+.1f}")

    return 0

if __name__ == "__main__":
    raise SystemExit(main())

