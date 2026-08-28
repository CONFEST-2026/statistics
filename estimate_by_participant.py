#!/usr/bin/env python3
"""Estimate per-participant travel distance and CO2e from the raw_participants
CSV, producing a publishable CSV with only numeric distance and CO2 columns.

Assumed input: a "raw_participants" CSV with (at least) these columns:
- travel_mode        ("train", "plane", "local", or blank if unspecified)
- origin_name         (city name; may be blank)
- country_code        (ISO alpha-2)
- origin_coordinates  ("lat,lon" string; may be blank)
- conference, workshop (used to derive the anonymised event category)

Output public CSV columns:
- country_code
- event (CONCUR, QEST+FORMATS, FMICS, or Workshops)
- train_km, plane_km, bus_km
- train_co2_kg, plane_co2_kg, bus_co2_kg, total_co2_kg

This script is intentionally minimal: all hard-coded routing data and
emission factors are at the top for easy review. It only operates on
already-sanitised data -- it never reads names, affiliations, or any raw
registration workbook.

Usage:
  python3 estimate_by_participant.py path/to/raw_participants.csv path/to/public.csv

Do NOT publish the input CSV; only publish the output CSV.
"""
from __future__ import annotations
import csv
import math
import sys
from pathlib import Path

CONFERENCES = {"CONCUR", "QEST+FORMATS", "FMICS"}
WORKSHOP_ALIASES = {
    "express/sos": "EXPRESS/SOS",
    "snr": "SNR",
    "trends": "Trends",
    "yr-concur": "YR-Concur",
}


def event_group(row: dict) -> str:
    conference = (row.get("conference") or "").strip()
    if conference in CONFERENCES:
        return conference
    workshop = (row.get("workshop") or "").strip()
    event = WORKSHOP_ALIASES.get(workshop.lower())
    if event is None:
        raise ValueError(
            f"Unrecognised event fields: conference={conference!r}, workshop={workshop!r}"
        )
    return "Workshops"

# --- Constants: adjust here if needed ---
LIVERPOOL = (53.4084, -2.9916)
TRAIN_EMISSION = 0.041  # kg CO2e per passenger-km
# Plane emission factor is distance-banded (kg CO2e per passenger-km per
# one-way leg), following labos1point5/ADEME (also used by comparable
# conference-footprint studies), including the effect of contrails:
PLANE_EMISSION_BANDS = (
    (1000, 0.258),   # one-way distance <= 1000 km
    (3500, 0.187),   # 1000 < one-way distance <= 3500 km
    (float("inf"), 0.152),  # one-way distance > 3500 km
)
BUS_EMISSION = 0.028  # kg CO2e per passenger-km (Our World in Data coach value)
MANCHESTER_BUS_RT_KM = 110  # round-trip bus distance Manchester<->Liverpool
LONGHAUL_COUNTRIES = {"AU", "US"}
# Detour/great-circle-vs-actual-routing correction applied to straight-line
# plane distances (also used to recover the one-way distance for banded
# emission factors -- see compute_co2).
#
# Rationale: all raw distances in this script are great-circle ("as the crow
# flies") distances between coordinates. Real flights are longer than the
# great circle because of airway structure, holding patterns, departure/arrival
# routings and taxiing, so every straight-line plane distance is multiplied by
# this factor. 1.10 (i.e. +10%) is the conventional correction used in
# aviation-footprint accounting. Note that comparable studies (e.g. the
# Highlights conference-footprint reports) use raw great-circle distances with
# no such correction, so our plane figures are ~10% higher than theirs for an
# otherwise identical journey.
PLANE_DETOUR_FACTOR = 1.10

# Equivalent correction for rail journeys whose distance is not known exactly.
# Track routes are considerably longer than the great circle (mountains, coast
# lines, and the fact that trains must follow existing lines via major hubs).
# Calibrated against the exact route mileages in RAIL_TO_LIVERPOOL below, whose
# rail/great-circle ratios range from 1.10 (London) to 1.41 (Edinburgh), with a
# median of 1.16 and a mean of 1.22.
RAIL_DETOUR_FACTOR = 1.20

# Fixed hub coordinates used as routing waypoints (not participant origins).
COORDS = {
    "London": (51.5074, -0.1278),
    "Manchester": (53.4808, -2.2426),
    "Paris": (48.8566, 2.3522),
    "Amsterdam": (52.3676, 4.9041),
    "Rotterdam": (51.9244, 4.4777),
    "Liverpool": LIVERPOOL,
}

# Simple rail legs (one-way km) for important segments; add if needed
RAIL_LEGS = {
    ("Rotterdam", "London"): 515,
    ("Paris", "London"): 491,
    ("London", "Liverpool"): 315,
    ("Amsterdam", "Rotterdam"): 69,
    # ('Rotterdam','Liverpool') is computed as Rotterdam->London + London->Liverpool
}

# Exact one-way rail route distances (km) from a domestic origin to Liverpool
# Lime Street, for every British origin that occurs in the registration data.
#
# Source: the RailMiles Mileage Engine (https://my.railmiles.me/mileage-engine/),
# which returns the shortest route over the Great Britain rail network between
# two TIPLOC locations, in miles and chains; the values below are those figures
# converted to km and rounded. Using them avoids the crude "everything routes
# via London" approximation, which grossly overstated northern origins (e.g.
# Edinburgh 818 km instead of 398 km) and nearby ones (Widnes 556 km instead of
# 22 km).
#
# Keys are matched case-insensitively as substrings of the origin name, so
# "Southampton UK" resolves via "southampton".
RAIL_TO_LIVERPOOL = {
    "london": 315,        # London Euston,           195mi 69ch
    "birmingham": 146,    # Birmingham New Street,    90mi 70ch
    "brighton": 395,      # Brighton,                245mi 28ch
    "coventry": 174,      # Coventry,                108mi 01ch
    "edinburgh": 398,     # Edinburgh,               247mi 01ch
    "glasgow": 399,       # Glasgow Central,         247mi 76ch
    "leicester": 189,     # Leicester,               117mi 39ch
    "oxford": 253,        # Oxford,                  157mi 21ch
    "reading": 297,       # Reading,                 184mi 52ch
    "southampton": 370,   # Southampton Central,     230mi 18ch
    # Widnes is on the Liverpool-Manchester (CLC) line, ~10 route miles from
    # Liverpool South Parkway; the mileage engine's free daily quota was
    # exhausted, so this is taken from published route distances instead.
    "widnes": 22,
}

# Eurostar hubs to consider for routing European origins to London
EUROSTAR_HUBS = ["Amsterdam", "Rotterdam", "Paris"]

# --------------------------------------

def haversine_km(a: tuple, b: tuple) -> float:
    lat1, lon1 = a
    lat2, lon2 = b
    r = 6371
    phi1, phi2 = math.radians(lat1), math.radians(lat2)
    dphi = math.radians(lat2 - lat1)
    dlambda = math.radians(lon2 - lon1)
    x = math.sin(dphi / 2) ** 2 + math.cos(phi1) * math.cos(phi2) * math.sin(dlambda / 2) ** 2
    return 2 * r * math.asin(math.sqrt(x))


def city_distance_km(a: str, b: str) -> int:
    """Straight-line distance between two named hubs, using COORDS.

    Raises ValueError with an actionable message when a hub is unknown.
    """
    if a not in COORDS or b not in COORDS:
        raise ValueError(f"Unknown hub coordinates for '{a}' or '{b}'. Add it to COORDS.")
    return int(round(haversine_km(COORDS[a], COORDS[b])))


def rail_leg_km(a: str, b: str) -> int:
    """Distance for a rail leg between two named hubs: the hard-coded
    RAIL_LEGS value if known, otherwise the straight-line distance inflated by
    RAIL_DETOUR_FACTOR (rail track is never straight -- see the constant)."""
    if (a, b) in RAIL_LEGS:
        return RAIL_LEGS[(a, b)]
    return int(round(city_distance_km(a, b) * RAIL_DETOUR_FACTOR))


def domestic_rail_km(origin_name: str, origin_coords: tuple[float, float] | None) -> int:
    """One-way rail distance from a British origin to Liverpool Lime Street.

    Uses the exact route mileage from RAIL_TO_LIVERPOOL when the origin is one
    of the known cities, and otherwise falls back to the great-circle distance
    inflated by RAIL_DETOUR_FACTOR.
    """
    name = (origin_name or "").strip().lower()
    for key, km in RAIL_TO_LIVERPOOL.items():
        if key in name:
            return km
    return int(round(origin_distance_km(origin_coords, "Liverpool") * RAIL_DETOUR_FACTOR))


def origin_distance_km(origin_coords: tuple[float, float], city: str) -> int:
    """Straight-line distance from an arbitrary origin (lat, lon) to a named hub.

    Raises ValueError if origin_coords is missing/invalid or the hub is
    unknown, rather than silently returning 0 -- the exporter is expected to
    provide origin_coordinates for every non-empty origin.
    """
    if not origin_coords:
        raise ValueError("Missing origin_coordinates for a row with a non-local origin. Ensure the exporter resolved coordinates for every origin/affiliation.")
    if city not in COORDS:
        raise ValueError(f"Unknown hub coordinates for '{city}'. Add it to COORDS.")
    return int(round(haversine_km(origin_coords, COORDS[city])))


def compute_mode_distances(origin_name: str | None, travel_mode: str | None, country_code: str | None, origin_coords: tuple[float, float] | None = None) -> tuple[int, int, int]:
    """Return (train_km_rt, plane_km_rt, bus_km_rt) round-trip distances in km.

    travel_mode may be blank/None; origin_name may be an arbitrary city name;
    origin_coords is an optional (lat, lon) tuple used for all straight-line
    distance calculations (more accurate than name-based lookups, and works
    for any city, not just the small set of hard-coded hubs). This minimal
    function implements the project's heuristics:
      - If travel_mode contains 'train' -> Train
      - If travel_mode contains 'local' or origin contains 'liverpool' -> Local (zero travel)
      - Otherwise infer Train for GB/FR country codes, else Plane
      - Plane legs: default to Manchester arrival + MANCHESTER_BUS_RT_KM road transfer
      - Long-haul (country in LONGHAUL_COUNTRIES): plane to London then train London->Liverpool
      - Domestic GB train legs: exact rail route mileage to Liverpool (RAIL_TO_LIVERPOOL)
      - Train legs from Europe: choose closest Eurostar hub (Amsterdam/Rotterdam/Paris) and route via London->Liverpool; if origin equals a hub, use hub->London->Liverpool

    All distances derived from coordinates are great-circle distances, and are
    inflated by PLANE_DETOUR_FACTOR (air) or RAIL_DETOUR_FACTOR (rail) to
    approximate real routings; see those constants for details.
    """
    origin_name = (origin_name or "").strip()
    travel_mode = (travel_mode or "").strip().lower()
    country = (country_code or "").strip().upper()

    # Local case
    if "liverpool" in origin_name.lower() or "local" in travel_mode or travel_mode in {"walk", "bike", "bicycle", "no travel"}:
        return 0, 0, 0

    # Explicit train/plane
    if "train" in travel_mode:
        mode = "train"
    elif "plane" in travel_mode:
        mode = "plane"
    else:
        mode = "train" if country in {"GB", "FR"} else "plane"

    # Train route
    if mode == "train":
        london_liv = rail_leg_km("London", "Liverpool")

        # Domestic UK train journeys go directly to Liverpool, using the exact
        # rail route mileage where we know it (see RAIL_TO_LIVERPOOL). They
        # must NOT be routed via London: that used to turn Widnes (22 km away)
        # into a 556 km journey and Edinburgh into 818 km.
        if country == "GB":
            total = int(round(domestic_rail_km(origin_name, origin_coords) * 2))
            return total, 0, 0

        # If origin is one of our known Eurostar hubs, approximate as
        # hub->London + London->Liverpool
        for hub in EUROSTAR_HUBS:
            if hub.lower() in origin_name.lower() or origin_name.lower() == hub.lower():
                hub_london = rail_leg_km(hub, "London")
                total = int(round((hub_london + london_liv) * 2))
                return total, 0, 0
        # Other European train origin: route via the closest Eurostar hub,
        # then hub->London->Liverpool (fixing the previous bug where the
        # Rotterdam leg omitted the London->Liverpool leg).
        best_hub_km = None
        for hub in EUROSTAR_HUBS:
            # Continental origin->hub is a rail leg of unknown exact length, so
            # apply the rail detour correction to the great-circle distance.
            origin_hub_km = origin_distance_km(origin_coords, hub) * RAIL_DETOUR_FACTOR
            hub_london = rail_leg_km(hub, "London")
            candidate = origin_hub_km + hub_london
            if best_hub_km is None or candidate < best_hub_km:
                best_hub_km = candidate
        total = int(round((best_hub_km + london_liv) * 2))
        return total, 0, 0

    # Plane route
    if mode == "plane":
        # Long-haul: fly to London then train to Liverpool
        if country in LONGHAUL_COUNTRIES:
            plane_oneway = origin_distance_km(origin_coords, "London")
            plane_rt = int(round(plane_oneway * 2 * PLANE_DETOUR_FACTOR))
            train_rt = int(round(rail_leg_km("London", "Liverpool") * 2))
            return train_rt, plane_rt, 0
        # Default: fly to Manchester + bus to Liverpool
        plane_oneway = origin_distance_km(origin_coords, "Manchester")
        plane_rt = int(round(plane_oneway * 2 * PLANE_DETOUR_FACTOR))
        bus_rt = int(round(MANCHESTER_BUS_RT_KM))
        return 0, plane_rt, bus_rt

    return 0, 0, 0


def _plane_emission_factor(one_way_km: float) -> float:
    """Distance-banded plane emission factor (kg CO2e/pkm) for a one-way leg."""
    for threshold, factor in PLANE_EMISSION_BANDS:
        if one_way_km <= threshold:
            return factor
    return PLANE_EMISSION_BANDS[-1][1]


def compute_co2(train_km: int, plane_km: int, bus_km: int) -> tuple[float, float, float, float]:
    t = round(train_km * TRAIN_EMISSION, 3)
    # plane_km is a round trip distance already inflated by PLANE_DETOUR_FACTOR;
    # recover the one-way great-circle leg to pick the correct emission band.
    plane_oneway = (plane_km / PLANE_DETOUR_FACTOR / 2) if plane_km else 0
    p = round(plane_km * _plane_emission_factor(plane_oneway), 3)
    b = round(bus_km * BUS_EMISSION, 3)
    return t, p, b, round(t + p + b, 3)


def main():
    if len(sys.argv) < 3:
        print("Usage: estimate_by_participant.py raw_participants.csv output_public.csv")
        raise SystemExit(2)
    inp = Path(sys.argv[1])
    out = Path(sys.argv[2])
    if not inp.exists():
        raise FileNotFoundError(f"Input CSV not found: {inp}")
    with inp.open(newline="") as handle:
        reader = csv.DictReader(handle)
        rows = list(reader)
    required_cols = {"origin_name", "travel_mode", "country_code"}
    if rows and not required_cols.issubset(reader.fieldnames or []):
        raise ValueError(
            f"Input CSV must be a raw_participants CSV: required columns are {sorted(required_cols)}, "
            f"got {reader.fieldnames}."
        )
    out_rows = []
    explicit_mode_count = 0
    inferred_mode_count = 0
    local_count = 0
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
        if mode.strip().lower() in {"train", "plane"}:
            explicit_mode_count += 1
        elif "liverpool" in origin_name.lower() or mode.strip().lower() == "local":
            local_count += 1
        else:
            inferred_mode_count += 1
        # compute distances; origin_distance_km/city_distance_km raise ValueError on missing data
        train_km, plane_km, bus_km = compute_mode_distances(origin_name, mode, country, origin_coords)
        train_kg, plane_kg, bus_kg, total_kg = compute_co2(train_km, plane_km, bus_km)
        out_row = {
            "country_code": country or "",
            "event": event_group(r),
            "train_km": train_km,
            "plane_km": plane_km,
            "bus_km": bus_km,
            "train_co2_kg": train_kg,
            "plane_co2_kg": plane_kg,
            "bus_co2_kg": bus_kg,
            "total_co2_kg": total_kg,
        }
        out_rows.append(out_row)
    out.parent.mkdir(parents=True, exist_ok=True)
    with out.open("w", newline="") as handle:
        # Explicit public columns (shortened names)
        fieldnames = [
            "country_code", "event",
            "train_km",
            "plane_km",
            "bus_km",
            "train_co2_kg",
            "plane_co2_kg",
            "bus_co2_kg",
            "total_co2_kg",
        ]
        writer = csv.DictWriter(handle, fieldnames=fieldnames)
        writer.writeheader()
        for r in out_rows:
            writer.writerow({k: r.get(k, "") for k in fieldnames})
    print(f"Wrote anonymised public CSV: {out} ({len(out_rows)} rows)")
    print(
        f"Audit: {explicit_mode_count} rows with explicit travel_mode, "
        f"{inferred_mode_count} rows with inferred (country-based) travel_mode, "
        f"{local_count} rows treated as local (zero travel)."
    )


if __name__ == '__main__':
    main()
