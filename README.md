# Surf Spot Climate Resilience Explorer — V1 skeleton

An interactive, map-based web tool that plots England's surf breaks and shows how
each is projected to be affected by climate change. This skeleton covers **two**
of the three PRD climate indicators:

- **Coastal erosion risk** — NCERM 2024 (Environment Agency, OGC API – Features)
- **Sea level rise** — Met Office UKCP18 (ArcGIS FeatureServer)

> Sea **temperature** change (PRD §4.4) is intentionally **deferred** from this
> iteration and is not implemented.

The architecture follows the PRD: a Python step queries the live open-data APIs
once and bakes the results into a static `data/surf_breaks.json`; the frontend is
dependency-free (Leaflet via CDN, OpenStreetMap/CARTO tiles, no API key) and makes
**no** API calls at runtime.

## Layout

```
surf_climate_explorer/
├── surf_breaks.csv      # curated seed breaks (id, name, latitude, longitude, region)
├── config.py            # endpoints, chosen scenarios, HTTP behaviour
├── preprocess.py        # queries the two APIs -> data/surf_breaks.json
├── data/
│   └── surf_breaks.json # generated artefact loaded by the frontend
└── web/
    ├── index.html       # map + profile panel shell
    ├── style.css        # ocean palette, mobile-first (side drawer / bottom sheet)
    └── app.js           # Leaflet map, markers, panel, sea-level sparkline
```

## 1. Generate the data

```powershell
pip install -r requirements.txt
python preprocess.py            # all breaks (~a few minutes; the EA server is slow)
python preprocess.py --limit 3  # quick smoke test
```

The script retries on failure and, if an indicator still can't be fetched for a
break, records it with a non-`ok` status and a null value rather than failing or
fabricating data. Re-run to refresh.

## 2. Run the website

The page fetches a local JSON file, so it must be served over HTTP (not opened
as a `file://` URL):

```powershell
python -m http.server 8000
# then open http://localhost:8000/web/
```

Click any break marker to open its profile panel.

## Data sources & scenarios

| Indicator | Source | Scenario used | Licence |
|---|---|---|---|
| Sea level rise | Met Office UKCP18 (ArcGIS) | RCP8.5, 50th percentile, vs 1981–2000 | OGL v3.0 |
| Coastal erosion | NCERM 2024 (Environment Agency, OGC API) | Higher Central allowance (70th pct, ~RCP8.5) | OGL v3.0 |

Scenario choices live in `config.py` (`SEA_LEVEL_RCP_PERCENTILE`, `EROSION_CC`).

## Notes & known limitations (this skeleton)

- **Marker colour** reflects long-term (to 2105) erosion risk derived from the
  NCERM SMP policy/interpretation: red = higher, amber = moderate, green =
  lower/managed, grey = not mapped nearby.
- **Erosion bands** are NCERM recession indicators (higher = more projected
  erosion); their absolute units are not interpreted here.
- **Nearest-frontage matching**: a break is matched to the closest NCERM frontage
  within `EROSION_MAX_DISTANCE_M` (2 km). Beyond that it is reported as "not
  mapped nearby" rather than risk-free — this is the V1 answer to PRD open
  question #2 (not-at-risk vs data gap).
- **Recession-zone inset**: the panel embeds a small Leaflet map showing the NCERM
  erosion-zone *polygons* near the break — the projected area of land at risk.
  With-SMP (the realistic, planned scenario) is shown by default as filled zones
  (amber = to 2055, red = to 2105); the No-Intervention ("no defences")
  counterfactual is a toggleable dashed outline. The zone footprint *is* the
  recession visualisation — we deliberately do **not** present the undocumented
  numeric band as a metres figure.
- **Geometry is simplified** (Ramer–Douglas–Peucker, ~3 m tolerance) and rounded
  to 5 dp before storage, keeping `surf_breaks.json` near ~1 MB instead of ~7 MB.
- **Sea level** polygons sit offshore, so the point is buffered outward until
  polygons are hit; overlapping polygons within the buffer are averaged.
- Deferred to later iterations: sea temperature, MMO1064 overlay, time-series
  uncertainty band, scenario toggles, filtering/benchmarking, Wales/Scotland.
