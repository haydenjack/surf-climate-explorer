"""Pre-process surf breaks into a static surf_breaks.json for the frontend.

For each curated surf break this script queries two live UK open-data APIs:

  * Met Office UKCP18 sea level projections (ArcGIS FeatureServer)
  * Environment Agency NCERM 2024 coastal erosion risk (OGC API - Features)

and bakes the results into ``data/surf_breaks.json``. No live queries are made
at runtime by the website; this script is run once during development (re-run
to refresh the data).

Design notes
------------
* The Environment Agency server is intermittently slow and returns 504s, so
  every request is retried with backoff. If an indicator cannot be fetched for
  a break after retries, that indicator is recorded with ``status`` other than
  ``"ok"`` and a null value, and the pipeline continues. The map therefore
  always renders, and missing data is clearly labelled rather than faked.
* Pure standard library + ``requests`` only (no geopandas/shapely/pyproj). The
  servers do the heavy spatial work; nearest-frontage selection uses a simple
  great-circle distance to polygon vertices, which is ample at this scale.

Usage
-----
    python preprocess.py            # process all breaks
    python preprocess.py --limit 3  # quick smoke test on the first 3 breaks
"""

from __future__ import annotations

import argparse
import csv
import json
import math
import sys
import time
from datetime import datetime, timezone

import requests

import config

SESSION = requests.Session()
SESSION.headers.update({"User-Agent": "surf-climate-explorer/0.1 (preprocess)"})


# --------------------------------------------------------------------------- #
# HTTP helpers
# --------------------------------------------------------------------------- #
def _get_json(url: str, params: dict, accept_json: bool = False):
    """GET ``url`` and parse JSON, retrying on failure. Returns None on give-up."""
    headers = {"Accept": "application/json"} if accept_json else None
    last = None
    for attempt in range(config.HTTP_RETRIES):
        try:
            resp = SESSION.get(
                url, params=params, headers=headers, timeout=config.HTTP_TIMEOUT_S
            )
            if resp.status_code == 200:
                time.sleep(config.INTER_REQUEST_DELAY_S)
                return resp.json()
            last = f"HTTP {resp.status_code}"
        except (requests.RequestException, ValueError) as exc:
            last = type(exc).__name__
        time.sleep(config.HTTP_BACKOFF_S * (attempt + 1))
    print(f"      ! request failed ({last}): {url}", file=sys.stderr)
    return None


# --------------------------------------------------------------------------- #
# Geometry helpers (pure python, no shapely)
# --------------------------------------------------------------------------- #
def _haversine_m(lon1: float, lat1: float, lon2: float, lat2: float) -> float:
    """Great-circle distance in metres between two WGS84 points."""
    r = 6_371_000.0
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dp = math.radians(lat2 - lat1)
    dl = math.radians(lon2 - lon1)
    a = math.sin(dp / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    return 2 * r * math.asin(math.sqrt(a))


def _iter_vertices(geometry: dict):
    """Yield (lon, lat) vertices from a GeoJSON Polygon or MultiPolygon."""
    if not geometry:
        return
    gtype = geometry.get("type")
    coords = geometry.get("coordinates", [])
    if gtype == "Polygon":
        rings = coords
    elif gtype == "MultiPolygon":
        rings = [ring for poly in coords for ring in poly]
    else:
        rings = []
    for ring in rings:
        for pt in ring:
            yield pt[0], pt[1]


def _min_distance_m(lon: float, lat: float, geometry: dict) -> float:
    """Minimum distance (m) from a point to any vertex of a polygon geometry."""
    best = math.inf
    for vlon, vlat in _iter_vertices(geometry):
        d = _haversine_m(lon, lat, vlon, vlat)
        if d < best:
            best = d
    return best


def _perp_dist_deg(p, a, b) -> float:
    """Perpendicular distance (degrees, lon scaled by cos lat) from p to segment a-b."""
    k = math.cos(math.radians((a[1] + b[1]) / 2)) or 1e-9
    ax, ay, bx, by, px, py = a[0] * k, a[1], b[0] * k, b[1], p[0] * k, p[1]
    dx, dy = bx - ax, by - ay
    if dx == 0 and dy == 0:
        return math.hypot(px - ax, py - ay)
    t = max(0.0, min(1.0, ((px - ax) * dx + (py - ay) * dy) / (dx * dx + dy * dy)))
    return math.hypot(px - (ax + t * dx), py - (ay + t * dy))


def _rdp(points: list, eps: float) -> list:
    """Iterative Ramer-Douglas-Peucker line simplification (avoids recursion limits)."""
    n = len(points)
    if n < 3:
        return points[:]
    keep = [False] * n
    keep[0] = keep[n - 1] = True
    stack = [(0, n - 1)]
    while stack:
        s, e = stack.pop()
        dmax, idx = 0.0, 0
        for i in range(s + 1, e):
            d = _perp_dist_deg(points[i], points[s], points[e])
            if d > dmax:
                dmax, idx = d, i
        if dmax > eps and idx:
            keep[idx] = True
            stack.append((s, idx))
            stack.append((idx, e))
    return [points[i] for i in range(n) if keep[i]]


def _clean_geometry(geometry: dict, ndigits: int = 5, eps: float = 0.00003):
    """Round, drop z, and simplify polygon rings so stored zones stay light.

    eps is in degrees (~3 m): preserves the zone shape at the inset map's scale
    while still cutting the vertex count substantially.
    """
    if not geometry:
        return None

    def _ring(ring):
        pts = [[round(p[0], ndigits), round(p[1], ndigits)] for p in ring]
        simp = _rdp(pts, eps) if len(pts) > 6 else pts
        if len(simp) < 4:
            return None  # degenerate after simplification
        if simp[0] != simp[-1]:
            simp.append(simp[0])
        return simp

    def _polygon(poly):
        rings = [r for r in (_ring(r) for r in poly) if r]
        return rings or None

    gtype = geometry.get("type")
    coords = geometry.get("coordinates", [])
    if gtype == "Polygon":
        cleaned = _polygon(coords)
    elif gtype == "MultiPolygon":
        cleaned = [p for p in (_polygon(p) for p in coords) if p] or None
    else:
        cleaned = coords or None
    return {"type": gtype, "coordinates": cleaned} if cleaned else None


# --------------------------------------------------------------------------- #
# Sea level rise
# --------------------------------------------------------------------------- #
def fetch_sea_level(lat: float, lon: float) -> dict:
    """Return decadal sea-level anomalies (cm) for the nearest coastal polygons."""
    out_fields = ",".join(f"seaLevelAnom_{d}" for d in config.SEA_LEVEL_DECADES)
    for buffer_m in config.SEA_LEVEL_BUFFERS_M:
        data = _get_json(
            f"{config.SEA_LEVEL_URL}/query",
            {
                "geometry": f"{lon},{lat}",
                "geometryType": "esriGeometryPoint",
                "inSR": "4326",
                "spatialRel": "esriSpatialRelIntersects",
                "distance": str(buffer_m),
                "units": "esriSRUnit_Meter",
                "where": f"rcp_percentile='{config.SEA_LEVEL_RCP_PERCENTILE}'",
                "outFields": out_fields,
                "returnGeometry": "false",
                "f": "json",
            },
        )
        if data is None:
            return {"status": "unavailable", "anomaly_cm": None}
        feats = data.get("features", [])
        if feats:
            # Average the (very similar) overlapping polygons within the buffer.
            anomaly = {}
            for decade in config.SEA_LEVEL_DECADES:
                vals = [
                    f["attributes"].get(f"seaLevelAnom_{decade}")
                    for f in feats
                    if f["attributes"].get(f"seaLevelAnom_{decade}") is not None
                ]
                anomaly[str(decade)] = round(sum(vals) / len(vals), 1) if vals else None
            return {
                "status": "ok",
                "scenario": "RCP8.5 50th percentile",
                "baseline": "1981-2000",
                "unit": "cm",
                "anomaly_cm": anomaly,
                "n_polygons": len(feats),
                "buffer_m": buffer_m,
            }
    # No polygons found even at the widest buffer.
    return {"status": "no_nearby_polygon", "anomaly_cm": None}


# --------------------------------------------------------------------------- #
# Coastal erosion risk
# --------------------------------------------------------------------------- #
def _fetch_nearest_frontage(collection: str, lat: float, lon: float):
    """Return (properties, distance_m, geometry) for the nearest NCERM frontage.

    Returns (None, inf, None) if the request fails or no frontage is in range.
    """
    for delta in config.EROSION_BBOX_DELTAS:
        bbox = f"{lon - delta},{lat - delta},{lon + delta},{lat + delta}"
        data = _get_json(
            f"{config.EROSION_BASE_URL}/collections/{collection}/items",
            {"bbox": bbox, "limit": "100"},
            accept_json=True,
        )
        if data is None:
            return None, math.inf, None  # request failed
        feats = data.get("features", [])
        if feats:
            best_props, best_dist, best_geom = None, math.inf, None
            for f in feats:
                d = _min_distance_m(lon, lat, f.get("geometry"))
                if d < best_dist:
                    best_dist = d
                    best_props = f.get("properties", {})
                    best_geom = f.get("geometry")
            return best_props, best_dist, best_geom
    return None, math.inf, None  # nothing in the area at any bbox size


def _risk_level(long_term_interpretation: str | None, long_term_policy: str | None) -> str:
    """Derive a simple low/moderate/high risk level for marker colour-coding."""
    interp = (long_term_interpretation or "").strip().lower()
    policy = (long_term_policy or "").strip().lower()
    if interp == "erosion unrestricted" or policy == "no active intervention":
        return "high"
    if interp == "stop maintaining" or policy == "managed realignment":
        return "moderate"
    if interp == "erosion restricted" or policy == "hold the line":
        return "low"
    return "unknown"


def _band(props: dict, collection: str):
    """Pull the numeric recession band field, whose name embeds scenario/year."""
    # Field names look like smp2055_70 / nfi2105_70 — derive from the collection.
    parts = collection.split("_")  # e.g. ["NCERM", "SMP", "2055", "70CC"]
    field = f"{parts[1].lower()}{parts[2]}_{parts[3].replace('CC', '')}"
    return props.get(field)


def fetch_erosion(lat: float, lon: float) -> dict:
    """Return erosion risk classification for the four PRD scenario/term combos."""
    result: dict = {
        "climate_allowance": f"Higher Central ({config.EROSION_CC}th pct, ~RCP8.5)",
        "status": "ok",
    }
    request_failed = False
    nearest_overall = math.inf

    for field, collection in config.EROSION_COLLECTIONS.items():
        props, dist, geom = _fetch_nearest_frontage(collection, lat, lon)
        if props is None and dist == math.inf:
            # Could be a failed request or genuinely no frontage; flag below.
            result[field] = None
            request_failed = request_failed or True
            continue
        nearest_overall = min(nearest_overall, dist)
        if dist > config.EROSION_MAX_DISTANCE_M:
            result[field] = None
            continue
        # The frontage polygon is the projected erosion-zone footprint; keep it
        # so the frontend can visualise the recession extent near the break.
        zone = _clean_geometry(geom)
        if "with_smp" in field:
            term = "mt" if "medium" in field else "lt"
            result[field] = {
                "policy": (props.get(f"{term}_smp") or "").strip() or None,
                "interpretation": (props.get(f"{term}_smp_int") or "").strip() or None,
                "band": _band(props, collection),
                "zone": zone,
            }
            # Capture the policy-unit context once, from the SMP frontage.
            result.setdefault("smp_name", (props.get("smp_name") or "").strip() or None)
            result.setdefault("smp_pu", (props.get("smp_pu") or "").strip() or None)
        else:  # no-intervention collections carry only the numeric band
            result[field] = {"band": _band(props, collection), "zone": zone}

    result["nearest_frontage_m"] = (
        round(nearest_overall) if nearest_overall != math.inf else None
    )

    # Decide an overall status.
    long_smp = result.get("long_term_with_smp") or {}
    if request_failed and all(
        result.get(k) is None for k in config.EROSION_COLLECTIONS
    ):
        result["status"] = "unavailable"
    elif nearest_overall == math.inf or nearest_overall > config.EROSION_MAX_DISTANCE_M:
        result["status"] = "no_nearby_frontage"
    result["risk_level"] = _risk_level(
        long_smp.get("interpretation"), long_smp.get("policy")
    )
    return result


# --------------------------------------------------------------------------- #
# Driver
# --------------------------------------------------------------------------- #
def load_breaks(csv_path: str) -> list[dict]:
    breaks = []
    with open(csv_path, newline="", encoding="utf-8") as fh:
        for row in csv.DictReader(fh):
            breaks.append(
                {
                    "id": int(row["id"]),
                    "name": row["name"].strip().title(),
                    "lat": float(row["latitude"]),
                    "lon": float(row["longitude"]),
                    "region": row["region"].strip().title(),
                }
            )
    return breaks


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--limit", type=int, default=None, help="process first N breaks")
    parser.add_argument("--csv", default=config.INPUT_CSV)
    parser.add_argument("--out", default=config.OUTPUT_JSON)
    args = parser.parse_args()

    breaks = load_breaks(args.csv)
    if args.limit:
        breaks = breaks[: args.limit]

    print(f"Processing {len(breaks)} surf breaks...")
    records = []
    for i, brk in enumerate(breaks, 1):
        print(f"  [{i}/{len(breaks)}] {brk['name']} ({brk['region']})")
        sea = fetch_sea_level(brk["lat"], brk["lon"])
        ero = fetch_erosion(brk["lat"], brk["lon"])
        print(
            f"      sea_level={sea['status']}  "
            f"erosion={ero['status']} ({ero.get('risk_level')})"
        )
        records.append({**brk, "sea_level": sea, "erosion": ero})

    payload = {
        "metadata": {
            "generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
            "version": "V1-skeleton",
            "scope": "England — sea level rise + coastal erosion (sea temperature deferred)",
            "sources": {
                "sea_level": {
                    "name": "Met Office UKCP18 Time-mean Sea Level Projections to 2100",
                    "scenario": "RCP8.5, 50th percentile",
                    "baseline": "1981-2000",
                    "endpoint": config.SEA_LEVEL_URL,
                    "licence": "Open Government Licence v3.0",
                },
                "erosion": {
                    "name": "National Coastal Erosion Risk Mapping (NCERM) 2024",
                    "publisher": "Environment Agency",
                    "climate_allowance": f"{config.EROSION_CC}th pct (Higher Central, ~RCP8.5)",
                    "endpoint": config.EROSION_BASE_URL,
                    "licence": "Open Government Licence v3.0",
                },
            },
            "count": len(records),
        },
        "breaks": records,
    }

    with open(args.out, "w", encoding="utf-8") as fh:
        json.dump(payload, fh, indent=2)
    print(f"\nWrote {len(records)} breaks to {args.out}")

    # Quick summary of data completeness.
    sl_ok = sum(1 for r in records if r["sea_level"]["status"] == "ok")
    er_ok = sum(1 for r in records if r["erosion"]["status"] == "ok")
    print(f"  sea level ok: {sl_ok}/{len(records)}   erosion ok: {er_ok}/{len(records)}")


if __name__ == "__main__":
    main()
