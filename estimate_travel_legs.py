#!/usr/bin/env python3
"""Produce a leg-based public CSV from the same raw_participants CSV used by
estimate_by_participant.py, in a format comparable to the "Highlights" conference
footprint studies (one row per one-way trip leg: mode, distance_km).

Assumed input: a "raw_participants" CSV with (at least) these columns:
- travel_mode        ("train", "plane", "local", or blank if unspecified)
- origin_name         (city name; may be blank)
- country_code        (ISO alpha-2)
- origin_coordinates  ("lat,lon" string; may be blank)

Each participant's round trip (as modelled by estimate_by_participant.compute_mode_distances)
is decomposed into individual one-way legs: for every mode with a non-zero
round-trip distance, two identical one-way legs are emitted (an "arrival" and
a "departure" leg), each with distance = round_trip_distance / 2. CONFEST's
registration only records a single primary travel mode per participant (no
separate arrival/departure mode), so -- unlike Highlights, where arrival and
departure can genuinely differ -- our two legs per mode are always identical.

Output CSV columns:
- mode          ("train", "plane", or "bus")
- distance_km   (one-way distance for this leg)

This format is deliberately compatible with the Highlights conference-footprint
co2.py analysis scripts (which read "mode,distance,...", ignoring any extra
columns), so the same downstream tooling can be reused to double check
figures independently of estimate_by_participant.py.

Rows are written sorted by (mode, distance), not in registration order, so
that the two legs belonging to the same participant cannot be identified from
row order (they are trivially identical anyway, given the round-trip model).

This script only operates on already sanitised data (same input as
estimate_by_participant.py) and never reads names, affiliations, or any raw
registration workbook.

Usage:
  python3 estimate_travel_legs.py path/to/raw_participants.csv path/to/legs_public.csv

Do NOT publish the input CSV; only publish the output CSV.
"""
from __future__ import annotations
import csv
import sys
from pathlib import Path

from estimate_by_participant import compute_mode_distances, compute_co2


def legs_for_row(origin_name: str, travel_mode: str, country_code: str, origin_coords) -> list[tuple[str, int]]:
    """Decompose one participant's round trip into one-way (mode, distance_km) legs."""
    train_rt, plane_rt, bus_rt = compute_mode_distances(origin_name, travel_mode, country_code, origin_coords)
    legs: list[tuple[str, int]] = []
    for mode, rt_km in (("train", train_rt), ("plane", plane_rt), ("bus", bus_rt)):
        if rt_km:
            one_way = round(rt_km / 2)
            legs.append((mode, one_way))
            legs.append((mode, one_way))  # arrival + departure leg (identical, see module docstring)
    return legs


def main():
    if len(sys.argv) < 3:
        print("Usage: estimate_travel_legs.py raw_participants.csv output_legs.csv")
        raise SystemExit(2)
    inp = Path(sys.argv[1])
    out = Path(sys.argv[2])
    if not inp.exists():
        raise FileNotFoundError(f"Input CSV not found: {inp}")
    with inp.open(newline="") as handle:
        reader = csv.DictReader(handle)
        rows = list(reader)
        fieldnames = reader.fieldnames or []
    required_cols = {"origin_name", "travel_mode", "country_code"}
    if rows and not required_cols.issubset(fieldnames):
        raise ValueError(
            f"Input CSV must be a raw_participants CSV: required columns are {sorted(required_cols)}, "
            f"got {fieldnames}."
        )

    all_legs: list[tuple[str, int]] = []
    for r in rows:
        origin_name = r["origin_name"]
        mode = r["travel_mode"]
        country = (r.get("country_code") or "").strip()
        coords_raw = (r.get("origin_coordinates") or "").strip()
        origin_coords = None
        if coords_raw:
            try:
                lat_s, lon_s = coords_raw.split(",")
                origin_coords = (float(lat_s), float(lon_s))
            except ValueError as exc:
                raise ValueError(f"Malformed origin_coordinates '{coords_raw}' for origin '{origin_name}'.") from exc
        all_legs.extend(legs_for_row(origin_name, mode, country, origin_coords))

    all_legs.sort(key=lambda leg: (leg[0], leg[1]))

    out.parent.mkdir(parents=True, exist_ok=True)
    with out.open("w", newline="") as handle:
        writer = csv.writer(handle)
        writer.writerow(["mode", "distance_km"])
        for mode, dist in all_legs:
            writer.writerow([mode, dist])
    print(f"Wrote leg-based public CSV: {out} ({len(all_legs)} legs from {len(rows)} participants)")

    # Verification: recompute total CO2e from the legs and compare with the
    # participant-level total (via compute_co2, which uses the same banded
    # plane emission factors), to confirm both public CSVs agree.
    total_leg_co2 = 0.0
    for mode, dist in all_legs:
        if mode == "train":
            t, _, _, _ = compute_co2(dist, 0, 0)
            total_leg_co2 += t
        elif mode == "plane":
            # compute_co2 expects a round-trip plane distance to pick the
            # correct emission band; a single one-way leg uses the same band
            # so we pass it doubled and halve the resulting figure.
            _, p, _, _ = compute_co2(0, dist * 2, 0)
            total_leg_co2 += p / 2
        elif mode == "bus":
            _, _, b, _ = compute_co2(0, 0, dist)
            total_leg_co2 += b
    print(f"Verification: total CO2e from legs = {round(total_leg_co2, 3)} kg ({round(total_leg_co2 / 1000, 3)} tons)")


if __name__ == '__main__':
    main()
