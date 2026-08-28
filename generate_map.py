#!/usr/bin/env python3
"""Generate a map of actual participant origin cities and how they travelled,
built directly from the raw_participants CSV (real per-participant origin
coordinates), similar in spirit to the "Highlights" conference-footprint maps.

Assumed input: a "raw_participants" CSV with (at least) these columns:
- travel_mode        ("train", "plane", "local", or blank if unspecified)
- origin_name         (city name; may be blank)
- country_code        (ISO alpha-2)
- origin_coordinates  ("lat,lon" string; may be blank)

This script reads the raw per-participant data (which may include names and
affiliations) purely to plot city-level travel patterns -- its OUTPUT never
contains names, affiliations, or attendee-identifying columns: only city
coordinates (averaged across participants at the same city), a participant
count per city, and the count travelling by each mode. Do not publish the
input CSV; only the output GeoJSON is meant to be published.

Multiple participants sharing a city are merged into a single point, grouped
by (city name, country) rather than raw coordinates -- individual
participants' coordinates can differ slightly even within the same city
(e.g. different universities within London), which would otherwise produce
several needlessly cluttered pins for one city. The merged pin is placed at
the average of its participants' coordinates. A dot on the map therefore
represents "N participants from this city", not one individual -- except for
cities with only one participant, where the dot inevitably shows that one
person's travel mode. This is the same trade-off the Highlights
conference-footprint maps accept (their published maps are also per-city
dots with mode information, not further aggregated).

Outputs (written next to this script):
- travel_origins_map.geojson: one Point feature per city, with properties
  city, country_code, n_participants, n_plane, n_train, and
  fraction_flying (0=nobody flew, 1=everybody flew from that city), plus an
  `events` object containing the same counts for each event category. Each
  participant is counted under a single main mode of travel (plane, train, or
  bus); participants with no travel distance (local to Liverpool) are
  excluded entirely, since the map is about how people travelled, which does
  not apply to them. This is the data source for the interactive map on the
  public statistics page.
Usage:
  python3 generate_map.py path/to/raw_participants.csv [output_dir]
"""
from __future__ import annotations
import csv
import json
import sys
from collections import defaultdict
from pathlib import Path

from estimate_by_participant import compute_mode_distances, event_group

def main_mode(train_km: int, plane_km: int, bus_km: int) -> str:
    """The single main mode of travel for this participant, for map-pin
    colouring. Plane is considered the main mode even for participants who
    also take a short bus transfer at the destination (e.g. Manchester ->
    Liverpool by bus), since that transfer is incidental to a flight, not
    their primary way of travelling. In this dataset, nobody's only
    transport leg is bus (it is always paired with a plane leg), so bus is
    not a category here; add it back if that ever changes. Returns "local"
    for participants with no travel distance.
    """
    if plane_km:
        return "plane"
    if train_km:
        return "train"
    return "local"


def color_for_fraction(fraction_flying: float) -> str:
    """Interpolate blue (0, nobody flies) -> red (1, everybody flies)."""
    r = round(255 * fraction_flying)
    b = round(255 * (1 - fraction_flying))
    return f"#{r:02x}00{b:02x}"


def _norm_city(name: str) -> str:
    return " ".join(name.strip().lower().split())


def aggregate(rows: list[dict]) -> dict:
    """Aggregate participants by (city name, country) rather than raw
    coordinates: participants from the same city can have slightly different
    coordinates (e.g. different universities within London), which would
    otherwise produce multiple, needlessly cluttered pins for the same city.
    Coordinates for the merged pin are the average of its participants'
    coordinates.

    Local participants (no travel distance) are excluded entirely: the map
    shows how people travelled to CONFEST, which is not meaningful for
    people who did not travel.
    """
    agg: dict = defaultdict(lambda: {
        "origin_name": "", "country_code": "", "n_participants": 0,
        "n_plane": 0, "n_train": 0, "lat_sum": 0.0, "lon_sum": 0.0,
        "events": defaultdict(lambda: {"n_participants": 0, "n_plane": 0, "n_train": 0}),
    })
    for row in rows:
        origin_name = (row.get("origin_name") or "").strip()
        country = (row.get("country_code") or "").strip()
        mode = row.get("travel_mode") or ""
        coords_raw = (row.get("origin_coordinates") or "").strip()
        if not coords_raw:
            raise KeyError(f"Missing origin_coordinates for row: {row}")
        try:
            lat_s, lon_s = coords_raw.split(",")
            coords = (float(lat_s), float(lon_s))
        except ValueError as exc:
            raise ValueError(f"Malformed origin_coordinates '{coords_raw}' for origin '{origin_name}'.") from exc

        train_km, plane_km, bus_km = compute_mode_distances(origin_name, mode, country, coords)
        main = main_mode(train_km, plane_km, bus_km)
        if main == "local":
            continue
        event = event_group(row)
        key = (_norm_city(origin_name), country)
        a = agg[key]
        a["origin_name"] = origin_name
        a["country_code"] = country
        a["n_participants"] += 1
        a["lat_sum"] += coords[0]
        a["lon_sum"] += coords[1]
        a[f"n_{main}"] += 1
        a["events"][event]["n_participants"] += 1
        a["events"][event][f"n_{main}"] += 1
    return agg


def write_geojson(agg: dict, out_path: Path) -> None:
    features = []
    for key in sorted(agg):
        a = agg[key]
        lat = a["lat_sum"] / a["n_participants"]
        lon = a["lon_sum"] / a["n_participants"]
        fraction_flying = a["n_plane"] / a["n_participants"] if a["n_participants"] else 0.0
        features.append({
            "type": "Feature",
            "geometry": {"type": "Point", "coordinates": [lon, lat]},
            "properties": {
                "city": a["origin_name"],
                "country_code": a["country_code"],
                "n_participants": a["n_participants"],
                "n_plane": a["n_plane"],
                "n_train": a["n_train"],
                "fraction_flying": round(fraction_flying, 3),
                "color": color_for_fraction(fraction_flying),
                "events": {
                    event: {
                        "n_participants": counts["n_participants"],
                        "n_plane": counts["n_plane"],
                        "n_train": counts["n_train"],
                    }
                    for event, counts in a["events"].items()
                },
            },
        })
    geojson = {"type": "FeatureCollection", "features": features}
    out_path.write_text(json.dumps(geojson, indent=2))
    print(f"Wrote {out_path} ({len(features)} cities)")


def main():
    if len(sys.argv) < 2:
        print("Usage: generate_map.py raw_participants.csv [output_dir]")
        raise SystemExit(2)
    csv_path = Path(sys.argv[1])
    out_dir = Path(sys.argv[2]) if len(sys.argv) > 2 else Path(__file__).parent
    if not csv_path.exists():
        raise FileNotFoundError(f"Input CSV not found: {csv_path}")
    with csv_path.open(newline="") as handle:
        rows = list(csv.DictReader(handle))
    agg = aggregate(rows)

    total = sum(a["n_participants"] for a in agg.values())
    flying = sum(a["n_plane"] for a in agg.values())
    print(f"{len(agg)} distinct cities, {total} participants, {flying} travelling by plane ({flying / total:.0%})")

    out_dir.mkdir(parents=True, exist_ok=True)
    write_geojson(agg, out_dir / "travel_origins_map.geojson")


if __name__ == "__main__":
    main()
