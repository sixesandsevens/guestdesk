#!/usr/bin/env python3
import json
import sys
import os

BOXES_JSON = os.environ.get(
    "GRIEVANCE_BOXES_JSON",
    "/opt/guestdesk/guestdesk/utils/grievance_boxes.json",
)


def nudge(field, dx=0, dy=0, dw=0, dh=0):
    with open(BOXES_JSON, "r") as f:
        boxes = json.load(f)
    if field not in boxes:
        raise SystemExit(f"Unknown field: {field}")
    x, y, w, h = boxes[field]
    boxes[field] = [x + dx, y + dy, w + dw, h + dh]
    with open(BOXES_JSON, "w") as f:
        json.dump(boxes, f, indent=2)
    print(f"{field}: [{x},{y},{w},{h}] -> {boxes[field]}")


if __name__ == "__main__":
    # usage: grievance_calibrate.py <field> <dx> <dy> [dw] [dh]
    if len(sys.argv) < 4:
        raise SystemExit("usage: grievance_calibrate.py <field> <dx> <dy> [dw] [dh]")
    field = sys.argv[1]
    dx, dy = float(sys.argv[2]), float(sys.argv[3])
    dw = float(sys.argv[4]) if len(sys.argv) > 4 else 0
    dh = float(sys.argv[5]) if len(sys.argv) > 5 else 0
    nudge(field, dx, dy, dw, dh)

