#!/usr/bin/env python3
import json
import os
import sys

P = "/opt/guestdesk/guestdesk/utils/grievance_boxes.json"

def main():
    dx = float(os.environ.get("GRV_GLOBAL_DX", "0") or 0)
    dy = float(os.environ.get("GRV_GLOBAL_DY", "0") or 0)
    if dx == 0 and dy == 0:
        print("GRV_GLOBAL_DX and GRV_GLOBAL_DY are both 0; nothing to apply.")
        return 0
    try:
        with open(P, "r") as f:
            boxes = json.load(f)
    except Exception as e:
        print("Failed to read boxes JSON:", e)
        return 1
    for k, v in list(boxes.items()):
        x, y, w, h = v
        boxes[k] = [x + dx, y + dy, w, h]
    try:
        with open(P, "w") as f:
            json.dump(boxes, f, indent=2)
    except Exception as e:
        print("Failed to write boxes JSON:", e)
        return 1
    print(f"Applied dx={dx} dy={dy} to all boxes in {P}.")
    return 0

if __name__ == "__main__":
    sys.exit(main())

