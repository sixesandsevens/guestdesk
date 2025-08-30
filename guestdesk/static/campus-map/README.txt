Interactive Map Toolkit (Author + Viewer)
========================================

What this is
------------
A tiny, no-dependency workflow for making a clickable campus map without hand‑tweaking
rotated rectangles. You digitize polygons (buildings/rooms/services) over a reference
image, tag them, and export to self-contained SVG or GeoJSON. The viewer page loads
your export and gives you zoom, search, and highlight.

Quick start
-----------
1) Author mode
   - Open `author/index.html` in a browser.
   - Click "Load background" and pick your PNG/JPG (e.g., the campus map).
   - Choose “Add polygon” and click to add vertices; double‑click to finish.
   - Click a polygon to select. Drag to move. Drag small circles to edit vertices.
   - Rotate: with a polygon selected, hold **R** and drag left/right to rotate
     around its centroid. (Use **Shift+R** for 15° increments.)
   - In the sidebar, set Name / Type / Services / Target.
   - Duplicate with **D**; Delete with **Delete**.
   - Export to **SVG** or **GeoJSON**.

2) Viewer mode
   - Copy your exported **SVG** to `viewer/overlays/overlay.svg` (create the folder).
     OR copy your **GeoJSON** to `viewer/data/features.json`.
   - Open `viewer/index.html`.
   - Pan/zoom with mouse; search by name or filter by tags in the sidebar.

Notes
-----
- The export embeds all geometry in document pixel coordinates: no GIS needed.
- You can use multiple exports per building/floor if you want deep drill-down later
  (e.g., link a building polygon’s `Target` to another viewer instance).
- This is meant to speed you up: you draw free polygons (not axis‑locked boxes),
  rotate with a gesture, and the export carries metadata.

Tips
----
- Build the *top-level* campus first with rough building footprints.
- Then make a separate author session per building interior using its floor plan.
- Keep a consistent `Type` set (building, room, service, outdoor, etc.).
- Use comma-separated list in Services for filtering (e.g., "laundry,wifi,showers").

Files you care about
--------------------
- `author/index.html`, `author/editor.js`, `author/styles.css`
- `viewer/index.html`, `viewer/viewer.js`, `viewer/styles.css`

