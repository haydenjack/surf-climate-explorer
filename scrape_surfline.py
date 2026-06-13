"""Scrape English surf spots from Surfline's public (undocumented) mapview API.

Surfline exposes an unauthenticated mapview endpoint that returns every surf
spot inside a bounding box, with name, coordinates, a spot id, the containing
subregion, and a `parentTaxonomy` ancestry. A single request over an
England-covering bbox returns all ~350 spots in the area (no pagination), so we
make ONE call, filter to England via the taxonomy hierarchy, and write CSV +
GeoJSON.

England filter
--------------
Each spot's `parentTaxonomy` is a list of taxonomy ids for its geographic
ancestry (Earth > Europe > United Kingdom > England > county > subregion > spot).
A spot is in England iff the England *region* taxonomy id is in that list. This
is authoritative and avoids guessing from subregion names (which include
ambiguous groupings like "West Coast and Isle of Man").

Notes
-----
* This is an undocumented endpoint; be polite. We make a single request, set a
  descriptive User-Agent, and expose a delay constant in case the bbox ever has
  to be split. Surfline's terms may restrict scraping — this is intended for
  personal, low-volume research use.
* `relivableRating` (0-5) is the only quality-ish signal in the payload and is 0
  for most spots; >=3 roughly flags Surfline's "premium"/cammed spots, a usable
  proxy for notable/popular. Stored so the frontend can optionally rank/filter.

Usage
-----
    python scrape_surfline.py
    python scrape_surfline.py --min-rating 3   # only notable spots
"""

from __future__ import annotations

import argparse
import csv
import json
import time

import requests

# England bounding box (PRD scope). Spills into Wales/Scotland/France — the
# taxonomy filter below removes non-English spots.
BBOX = {"south": 49.5, "west": -6.5, "north": 55.9, "east": 2.0}
MAPVIEW_URL = "https://services.surfline.com/kbyg/mapview"
# Taxonomy id for the England *region* node (type=region, name=England).
ENGLAND_TAXONOMY_ID = "5908d78edadb30820b3ba228"
# Subregions to drop even though Surfline tags them as England: the Channel
# Islands lie outside NCERM's England-only erosion mapping, so they'd never get
# coastal-erosion data.
EXCLUDE_SUBREGIONS = {"Channel Islands-England"}
REQUEST_DELAY_S = 1.0  # politeness; only matters if the bbox is ever split
HEADERS = {"User-Agent": "surf-climate-explorer/0.1 (personal research)"}

OUT_CSV = "data/english_surf_spots.csv"
OUT_GEOJSON = "data/english_surf_spots.geojson"


def fetch_spots() -> list[dict]:
    """Return all spots in the England bbox from a single mapview request."""
    resp = requests.get(MAPVIEW_URL, params=BBOX, headers=HEADERS, timeout=60)
    resp.raise_for_status()
    time.sleep(REQUEST_DELAY_S)
    return resp.json().get("data", {}).get("spots", [])


def to_record(spot: dict) -> dict:
    """Flatten a Surfline spot to the fields we keep."""
    return {
        "spot_id": spot.get("_id"),
        "name": (spot.get("name") or "").strip(),
        "lat": spot.get("lat"),
        "lon": spot.get("lon"),
        "subregion": (spot.get("subregion") or {}).get("name"),
        "relivable_rating": spot.get("relivableRating") or 0,
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--min-rating",
        type=float,
        default=0,
        help="keep only spots with relivableRating >= this (e.g. 3 for notable)",
    )
    args = parser.parse_args()

    spots = fetch_spots()
    english = [
        s
        for s in spots
        if ENGLAND_TAXONOMY_ID in s.get("parentTaxonomy", [])
        and (s.get("subregion") or {}).get("name") not in EXCLUDE_SUBREGIONS
    ]
    records = [to_record(s) for s in english]
    if args.min_rating:
        records = [r for r in records if (r["relivable_rating"] or 0) >= args.min_rating]
    # Dedupe by spot_id and sort for stable output.
    records = list({r["spot_id"]: r for r in records}.values())
    records.sort(key=lambda r: (r["subregion"] or "", r["name"]))

    fields = ["spot_id", "name", "lat", "lon", "subregion", "relivable_rating"]
    with open(OUT_CSV, "w", newline="", encoding="utf-8") as fh:
        writer = csv.DictWriter(fh, fieldnames=fields)
        writer.writeheader()
        writer.writerows(records)

    geojson = {
        "type": "FeatureCollection",
        "features": [
            {
                "type": "Feature",
                "geometry": {"type": "Point", "coordinates": [r["lon"], r["lat"]]},
                "properties": {k: r[k] for k in fields if k not in ("lat", "lon")},
            }
            for r in records
            if r["lat"] is not None and r["lon"] is not None
        ],
    }
    with open(OUT_GEOJSON, "w", encoding="utf-8") as fh:
        json.dump(geojson, fh, indent=2)

    print(
        f"Fetched {len(spots)} spots in bbox; {len(english)} in England; "
        f"wrote {len(records)} records"
        + (f" (relivable_rating >= {args.min_rating})" if args.min_rating else "")
    )
    print(f"  {OUT_CSV}")
    print(f"  {OUT_GEOJSON}")


if __name__ == "__main__":
    main()
