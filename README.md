# CONFEST 2026 — statistics (public)

This repository holds the **public** anonymised travel/CO2 dataset for
CONFEST 2026, together with the scripts used to derive it, and the scripts
that turn it into a leg-based CSV and origin maps.

Nothing here contains names, affiliations, or exact addresses.

The repository is published as a single squashed snapshot commit. When the
public data changes, replace the existing commit rather than retaining
history, so older anonymisation states cannot be recovered from Git history.

## Contents

- `estimate_by_participant.py` — derives per-participant round-trip
  distance/CO2e estimates by mode of travel and an anonymised event category
  from a `raw_participants` CSV
  (see "Input format" below). Produces `participants.csv`.
- `estimate_travel_legs.py` — reshapes the same per-participant estimates
  into a comparable leg-based CSV (`trips_by_leg.csv`), similar in spirit to
  the CONFEST HIGHLIGHTS format.
- `generate_map.py` — produces the origin-city map data
  (`travel_origins_map.geojson`).
- `participants.csv` (including the anonymised `event` category),
  `trips_by_leg.csv`, `travel_origins_map.geojson` (including event-specific
  city aggregates) — the published data files, regenerated whenever the raw
  participants CSV changes.

## Methodology and script documentation

The estimation and map-generation scripts contain the complete, reviewable
constants, routing assumptions, input columns, and output schemas. Their
module docstrings document the command-line interface and input/output format;
the usage commands below show how to run the complete public pipeline.

### Distances and detour factors

All distances derived from coordinates are great-circle ("as the crow flies")
distances, which understate the real journey. Two corrections are applied, both
defined as named constants at the top of `estimate_by_participant.py`:

- `PLANE_DETOUR_FACTOR = 1.10` — every straight-line flight distance is
  inflated by 10% to account for airway structure, departure/arrival routings,
  holding and taxiing. This is also used to recover the one-way great-circle
  distance when picking the banded emission factor.
- `RAIL_DETOUR_FACTOR = 1.20` — applied to rail legs whose exact length is not
  known (currently only continental origin→Eurostar-hub legs).

British domestic rail journeys do **not** use a detour factor: `RAIL_TO_LIVERPOOL`
hard-codes the exact one-way route mileage from each British origin in the data
to Liverpool Lime Street, taken from the [RailMiles Mileage
Engine](https://my.railmiles.me/mileage-engine/) (shortest route over the GB
network, in miles and chains, converted to km). The observed rail/great-circle
ratios for those routes range from 1.10 (London) to 1.41 (Edinburgh), with a
median of 1.16 and a mean of 1.22 — which is what `RAIL_DETOUR_FACTOR` is
calibrated against.

Note that some comparable conference-footprint studies use uncorrected
great-circle distances throughout, so their totals are correspondingly lower
for otherwise identical journeys.

## Where the input data comes from

All figures are derived from the travel information participants supplied in
their conference registration responses: their stated main mode of travel and
the city they travel from. The registration responses themselves are not
published; only the anonymised outputs in this repository are.

### Input format

The scripts read a `raw_participants` CSV with the following columns. Only
`travel_mode`, `origin_name`, `country_code` and `origin_coordinates` affect
the distance and CO2e estimates; the remaining columns are ignored by the
scripts in this repository.

```csv
attendee,affiliation,conference,workshop,travel_mode,origin_name,country_code,origin_coordinates
A. Nonymous,Example University,CONCUR,,train,Edinburgh,GB,"55.9533,-3.1883"
B. Eispiel,Institute of Examples,FMICS,EXPRESS/SOS,plane,Tokyo,JP,"35.6762,139.6503"
```

- `travel_mode` — `train`, `plane`, `local` (or blank, in which case the mode
  is inferred from `country_code`).
- `origin_name` — the city travelled from; an origin containing "Liverpool"
  is treated as local, i.e. zero travel.
- `country_code` — ISO 3166-1 alpha-2.
- `origin_coordinates` — `"lat,lon"`, used for all great-circle distances and computed from `origin_name`.

## Usage

```
python3 estimate_by_participant.py raw_participants.csv participants.csv
python3 estimate_travel_legs.py raw_participants.csv trips_by_leg.csv
python3 generate_map.py raw_participants.csv .
```
