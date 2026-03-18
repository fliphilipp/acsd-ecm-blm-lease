#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""
generate_lease_deliverables.py

Purpose
-------
Create:
  1) A BLM-style "Description of Land" consisting of:
     - PLSS aliquot-part context (subdivisions, section, township, range, meridian)
     - A metes-and-bounds description (caption/preamble, body, and clauses)
     - A "choices and methods" narrative explaining decisions made to ensure the
       description is susceptible to one, and only one, interpretation.

  2) ESRI Shapefiles in EPSG:4326:
     - lease_boundary.shp (polygon)
     - lease_points.shp (points) including:
         * lease_boundary_point_of_beginning
         * lease_boundary_point_1 ... lease_boundary_point_4
         * signage_{id} for each signage record

Outputs (default: ./out/)
------------------------
out/
  lease_boundary.shp (+ .dbf/.shx/.prj/.cpg)
  lease_points.shp   (+ .dbf/.shx/.prj/.cpg)
  description_of_land.txt
  choices_and_methods.txt
  boundary_corners.csv
  boundary_courses.csv
  metadata.json

Notes on compliance intent
--------------------------
This script follows the Bureau of Land Management "Specifications for Descriptions of Land" style:
  - clear, unambiguous description structure
  - explicit Point of Beginning
  - bearings formatted with ° ' " symbols
  - area statement wording and approximate rounding
  - coordinate/courses tables to support coordinate-based boundary definitions

IMPORTANT LIMITATION
--------------------
The boundary coordinates were digitized from online mapping tools (not a field survey).
The script therefore includes explicit qualification language and does not claim survey accuracy.
"""

from __future__ import annotations

import argparse
import json
import math
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Tuple

import pandas as pd
import geopandas as gpd
from pyproj import Geod
from shapely.geometry import Point, Polygon
from shapely.validation import explain_validity


# =============================================================================
# Editable inputs (update these dictionaries/lists for future projects)
# =============================================================================

LEASE_PLSS: Dict[str, Any] = {
    "state": "California",
    "principal_meridian_name": "San Bernardino Meridian",
    "township": {"number": 15, "dir": "S"},  # "15s" -> 15 S
    "range": {"number": 1, "dir": "E"},      # "1e"  -> 1 E
    "section": 1,
    # Accept either "NE1/4 of NE1/4" or "NE1/4NE1/4"; script normalizes
    "subdivisions": [
        "NE1/4 of NE1/4",
        "SE1/4 of NE1/4",
        "NE1/4 of SE1/4",
    ],
}

# EPSG:4326 in input lat, lon order (as provided)
BOUNDARY_VERTICES: List[Dict[str, Any]] = [
    {"pt_id": 0, "lat": 32.90179, "lon": -116.82372, "is_pob": True},
    {"pt_id": 1, "lat": 32.89033, "lon": -116.82374, "is_pob": False},
    {"pt_id": 2, "lat": 32.89166, "lon": -116.82846, "is_pob": False},
    {"pt_id": 3, "lat": 32.89727, "lon": -116.82810, "is_pob": False},
    {"pt_id": 4, "lat": 32.90173, "lon": -116.82589, "is_pob": False},
]

SIGNAGE: List[Dict[str, Any]] = [
    {"id": "entrance_kiosk", "lat": 32.89052, "lon": -116.82375, "type": "informational;boundary", "status": "proposed"},
    {"id": "to_toe", "lat": 32.89173, "lon": -116.82615, "type": "directional", "status": "existing"},
    {"id": "south_ridge_trail", "lat": 32.89152, "lon": -116.82718, "type": "directional", "status": "existing"},
    {"id": "keep_out_el_cap_preserve", "lat": 32.90171, "lon": -116.82534, "type": "regulatory;boundary", "status": "proposed"},
    {"id": "cnf_blm_lease_boundary", "lat": 32.89565, "lon": -116.82374, "type": "boundary", "status": "proposed"},
    {"id": "to_toe_1st_tier", "lat": 32.89720, "lon": -116.82430, "type": "directional", "status": "existing"},
    {"id": "to_2nd_tier_regalias", "lat": 32.89960, "lon": -116.82417, "type": "directional", "status": "existing"},
]

COORDINATE_REFERENCE: Dict[str, Any] = {
    "epsg": 4326,
    "datum": "WGS 84",
    "epoch_or_adjustment_date": "NOT PROVIDED",  # Not provided in inputs. Keep explicit rather than guessing.
    "geoid_model": "N/A (no elevations used)",  # No elevations/Z are used; geoid is not applicable to horizontal-only output.
    "input_axis_order": "latitude, longitude (decimal degrees)",
    "geometry_axis_order": "x=longitude, y=latitude (standard GIS convention)",
}

LINEAGE_AND_QUALITY: Dict[str, Any] = {
    "source_summary": (
        "Corner coordinates were digitized from online mapping software using "
        "government land boundary overlays; coordinates are not from a field survey."
    ),
    "positional_accuracy": "UNKNOWN (digitized / not surveyed)",
    "intended_use": "Administrative/GIS depiction and draft description support.",
}

# Computation/format defaults (you may adjust)
COMPUTATION_SETTINGS: Dict[str, Any] = {
    "traverse_direction": "clockwise",
    "distance_units": "us_survey_feet",  # or "meters"
    "distance_decimals": 2,              # formatting only; does not imply survey-grade accuracy
    "bearing_seconds_decimals": 2,
    "area_rounding": "nearest_full_acre",  # for approximate area clause
}

LEASE_PLSS: Dict[str, Any] = {}
BOUNDARY_VERTICES: List[Dict[str, Any]] = []
SIGNAGE: List[Dict[str, Any]] = []
COORDINATE_REFERENCE: Dict[str, Any] = {}
LINEAGE_AND_QUALITY: Dict[str, Any] = {}
COMPUTATION_SETTINGS: Dict[str, Any] = {}


# =============================================================================
# Helpers
# =============================================================================

@dataclass(frozen=True)
class Vertex:
    pt_id: int
    lat: float
    lon: float
    is_pob: bool


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def normalize_subdivision(s: str) -> str:
    """
    Normalize PLSS subdivision text toward BLM symbol form.
    Examples:
      "NE1/4 of NE1/4" -> "NE1/4NE1/4"
      "NE1/4NE1/4"     -> "NE1/4NE1/4"
    """
    s2 = s.strip()
    s2 = s2.replace(" OF ", " of ").replace(" Of ", " of ")
    s2 = s2.replace(" of ", "")
    s2 = s2.replace(" ", "")
    return s2


def validate_inputs(plss: Dict[str, Any], vertices_raw: List[Dict[str, Any]], signage_raw: List[Dict[str, Any]]) -> List[Vertex]:
    if "principal_meridian_name" not in plss or not plss["principal_meridian_name"]:
        raise ValueError("PLSS: principal_meridian_name is required.")

    pob_count = sum(1 for v in vertices_raw if bool(v.get("is_pob")))
    if pob_count != 1:
        raise ValueError(f"Boundary must have exactly one Point of Beginning; found {pob_count}.")

    seen = set()
    vertices: List[Vertex] = []
    for v in vertices_raw:
        pt_id = int(v["pt_id"])
        if pt_id in seen:
            raise ValueError(f"Duplicate boundary pt_id: {pt_id}")
        seen.add(pt_id)

        lat = float(v["lat"])
        lon = float(v["lon"])
        if not (-90 <= lat <= 90):
            raise ValueError(f"Latitude out of range for pt_id {pt_id}: {lat}")
        if not (-180 <= lon <= 180):
            raise ValueError(f"Longitude out of range for pt_id {pt_id}: {lon}")

        vertices.append(Vertex(pt_id=pt_id, lat=lat, lon=lon, is_pob=bool(v.get("is_pob"))))

    vertices.sort(key=lambda x: x.pt_id)
    if vertices[0].pt_id != 0:
        # Not strictly required, but our convention uses Point 0 as POB.
        # Enforce so future edits don't silently change meaning.
        raise ValueError("Expected pt_id 0 to exist and be first after sorting. (Your POB is Point 0.)")

    signage_ids = [s["id"] for s in signage_raw]
    if len(signage_ids) != len(set(signage_ids)):
        raise ValueError("Duplicate signage IDs detected.")

    for s in signage_raw:
        lat = float(s["lat"])
        lon = float(s["lon"])
        if not (-90 <= lat <= 90) or not (-180 <= lon <= 180):
            raise ValueError(f"Signage coordinate out of range for id={s['id']}")

    return vertices


def us_survey_feet_from_meters(m: float) -> float:
    # 1 US survey foot = 0.3048006096... meters
    return m / 0.3048006096


def dms_components(angle_deg: float) -> Tuple[int, int, float]:
    """Return (deg, min, sec) for a positive angle in degrees."""
    angle_deg = abs(angle_deg)
    deg = int(angle_deg)
    minutes_float = (angle_deg - deg) * 60.0
    minute = int(minutes_float)
    second = (minutes_float - minute) * 60.0
    return deg, minute, second


def format_lat_lon_dms(lat: float, lon: float, sec_decimals: int = 2) -> str:
    lat_hemi = "North" if lat >= 0 else "South"
    lon_hemi = "East" if lon >= 0 else "West"
    d1, m1, s1 = dms_components(lat)
    d2, m2, s2 = dms_components(lon)
    s1f = f"{s1:.{sec_decimals}f}"
    s2f = f"{s2:.{sec_decimals}f}"
    return f"{d1:02d}° {m1:02d}' {s1f}\" {lat_hemi}, {d2:03d}° {m2:02d}' {s2f}\" {lon_hemi}"


def azimuth_to_quadrant_bearing(az_deg: float, sec_decimals: int = 2) -> str:
    """
    Convert azimuth (degrees clockwise from true north) to a quadrant bearing string.
    """
    az = (az_deg + 360.0) % 360.0
    if 0.0 <= az < 90.0:
        ns, ew, ang = "North", "East", az
    elif 90.0 <= az < 180.0:
        ns, ew, ang = "South", "East", 180.0 - az
    elif 180.0 <= az < 270.0:
        ns, ew, ang = "South", "West", az - 180.0
    else:
        ns, ew, ang = "North", "West", 360.0 - az

    d, m, s = dms_components(ang)
    sf = f"{s:.{sec_decimals}f}"
    return f"{ns} {d:02d}° {m:02d}' {sf}\" {ew}"


def ring_is_ccw(coords_xy: List[Tuple[float, float]]) -> bool:
    """
    Planar ring orientation test (shoelace). For small extents, lon/lat is adequate for orientation.
    Returns True if counterclockwise.
    """
    s = 0.0
    for (x1, y1), (x2, y2) in zip(coords_xy, coords_xy[1:] + coords_xy[:1]):
        s += (x2 - x1) * (y2 + y1)
    # This sign convention yields s < 0 for CCW in this formula form
    return s < 0


def build_polygon(vertices: List[Vertex], enforce_clockwise: bool = True) -> Tuple[Polygon, List[Vertex]]:
    """
    Build polygon using x=lon, y=lat.
    Ensures:
      - POB remains first vertex
      - ring is clockwise if enforce_clockwise
      - polygon validity is checked
    """
    ordered = vertices[:]  # already sorted by pt_id
    coords = [(v.lon, v.lat) for v in ordered]

    if enforce_clockwise:
        if ring_is_ccw(coords):
            # Reverse while keeping first vertex (POB) fixed.
            pob = ordered[0]
            rest = list(reversed(ordered[1:]))
            ordered = [pob] + rest
            coords = [(v.lon, v.lat) for v in ordered]

    poly = Polygon(coords)
    if poly.is_empty:
        raise ValueError("Polygon geometry is empty.")
    if not poly.is_valid:
        raise ValueError(f"Polygon is invalid: {explain_validity(poly)}")
    if poly.area == 0:
        raise ValueError("Polygon has zero area (check duplicate points or ordering).")
    return poly, ordered


def compute_courses(vertices: List[Vertex], geod: Geod, COMPUTATION_SETTINGS=COMPUTATION_SETTINGS) -> pd.DataFrame:
    rows: List[Dict[str, Any]] = []
    n = len(vertices)
    for i in range(n):
        a = vertices[i]
        b = vertices[(i + 1) % n]
        fwd_az, back_az, dist_m = geod.inv(a.lon, a.lat, b.lon, b.lat)

        rows.append({
            "from_pt": a.pt_id,
            "to_pt": b.pt_id,
            "from_name": f"Point {a.pt_id}",
            "to_name": f"Point {b.pt_id}",
            "forward_azimuth_deg": float(fwd_az),
            "back_azimuth_deg": float(back_az),
            "bearing": azimuth_to_quadrant_bearing(float(fwd_az), sec_decimals=COMPUTATION_SETTINGS["bearing_seconds_decimals"]),
            "distance_m": float(dist_m),
            "distance_us_survey_ft": float(us_survey_feet_from_meters(dist_m)),
        })
    return pd.DataFrame(rows)


def compute_geodesic_area(poly: Polygon, geod: Geod) -> Tuple[float, float]:
    """
    Returns (area_m2, perimeter_m). Area may be negative depending on ring orientation; use abs.
    """
    area_m2, perim_m = geod.geometry_area_perimeter(poly)
    return abs(area_m2), perim_m


def acres_from_m2(m2: float) -> float:
    return m2 / 4046.8564224


def format_plss_block(plss: Dict[str, Any]) -> str:
    mer = plss["principal_meridian_name"]
    state = plss.get("state", "").strip() or "STATE NOT PROVIDED"

    t = plss["township"]
    r = plss["range"]
    sec = int(plss["section"])
    subs_raw = plss.get("subdivisions", [])
    subs = [normalize_subdivision(s) for s in subs_raw]

    # Render subdivisions with commas and a final "and" per BLM style.
    if len(subs) == 0:
        subs_text = "SUBDIVISIONS NOT PROVIDED"
    elif len(subs) == 1:
        subs_text = subs[0]
    elif len(subs) == 2:
        subs_text = f"{subs[0]} and {subs[1]}"
    else:
        subs_text = ", ".join(subs[:-1]) + f", and {subs[-1]}"

    return (
        f"{mer}, {state}\n"
        f"T. {int(t['number'])} {t['dir']}., R. {int(r['number'])} {r['dir']}.,\n"
        f"sec. {sec}, {subs_text},\n"
        "that portion described as follows:"
    )


def format_area_clause(area_acres: float, COMPUTATION_SETTINGS=COMPUTATION_SETTINGS) -> str:
    if COMPUTATION_SETTINGS["area_rounding"] == "nearest_full_acre":
        approx = int(math.floor(area_acres + 0.5))
        return f"The area described contains approximately {approx} acres."
    return f"The area described contains {area_acres:.2f} acres."


def make_description_of_land(
    plss: Dict[str, Any],
    vertices: List[Vertex],
    courses: pd.DataFrame,
    area_acres: float,
    COMPUTATION_SETTINGS=COMPUTATION_SETTINGS,
    COORDINATE_REFERENCE=COORDINATE_REFERENCE,
    LINEAGE_AND_QUALITY=LINEAGE_AND_QUALITY
) -> str:
    """
    Produce a compact BLM-style description:
      - PLSS preamble in tabular form
      - metes-and-bounds with BEGINNING/THENCE calls
      - clauses: area, basis of bearings, coordinate reference, qualification
    """
    plss_block = format_plss_block(plss)

    pob = next(v for v in vertices if v.is_pob)
    pob_dms = format_lat_lon_dms(pob.lat, pob.lon, sec_decimals=2)

    # Choose distance column
    use_ft = (COMPUTATION_SETTINGS["distance_units"] == "us_survey_feet")
    dist_col = "distance_us_survey_ft" if use_ft else "distance_m"
    dist_unit = "U.S. survey feet" if use_ft else "meters"

    lines: List[str] = []
    lines.append(plss_block)
    lines.append("")
    lines.append(
        f"BEGINNING at Point {pob.pt_id} (POINT OF BEGINNING), having geographic coordinates {pob_dms};"
    )

    for i, row in courses.iterrows():
        bearing = row["bearing"]
        dist = float(row[dist_col])
        dist_fmt = f"{dist:.{COMPUTATION_SETTINGS['distance_decimals']}f}"
        to_pt = int(row["to_pt"])

        # For the final course, explicitly close to POB.
        if to_pt == pob.pt_id:
            lines.append(
                f"THENCE, {bearing}, a distance of {dist_fmt} {dist_unit}, returning to the POINT OF BEGINNING."
            )
        else:
            lines.append(
                f"THENCE, {bearing}, a distance of {dist_fmt} {dist_unit}, to Point {to_pt};"
            )

    lines.append("")
    lines.append(format_area_clause(area_acres, COMPUTATION_SETTINGS=COMPUTATION_SETTINGS))
    lines.append("")
    lines.append("BASIS OF BEARINGS: Bearings stated herein are geodetic bearings computed")
    lines.append("from the listed corner coordinates on the WGS 84 ellipsoid and are referenced")
    lines.append("to true (geodetic) north.")
    lines.append("")
    lines.append("COORDINATE REFERENCE:")
    lines.append(f"  CRS: EPSG:{COORDINATE_REFERENCE['epsg']} (WGS 84 geographic coordinates)")
    lines.append(f"  Datum: {COORDINATE_REFERENCE['datum']}")
    lines.append(f"  Epoch/Date of adjustment: {COORDINATE_REFERENCE['epoch_or_adjustment_date']}")
    lines.append(f"  Geoid model: {COORDINATE_REFERENCE['geoid_model']}")
    lines.append("")
    lines.append("QUALIFICATION / LINEAGE:")
    lines.append(f"  {LINEAGE_AND_QUALITY['source_summary']}")
    lines.append(f"  Positional accuracy: {LINEAGE_AND_QUALITY['positional_accuracy']}")
    lines.append("")
    lines.append("SUPPORTING TABLES:")
    lines.append("  Corner coordinate and course tables are provided as separate CSV files in this deliverable package.")
    return "\n".join(lines)


def make_choices_and_methods(vertices: List[Vertex], poly: Polygon, courses: pd.DataFrame, area_acres: float, COMPUTATION_SETTINGS=COMPUTATION_SETTINGS) -> str:
    """
    Explain decisions made to ensure one interpretation.
    Intended as supporting documentation, not the legal description itself.
    """
    pob = next(v for v in vertices if v.is_pob)

    txt: List[str] = []
    txt.append("Choices and Methods (supporting documentation)")
    txt.append("")
    txt.append("Goal:")
    txt.append("  Produce a draft land description and GIS layers that are reproducible and")
    txt.append("  susceptible to one, and only one, interpretation by clearly defining all")
    txt.append("  computational and drafting choices.")
    txt.append("")
    txt.append("Key interpretive choices fixed in this package:")
    txt.append(f"  - Point of Beginning: Point {pob.pt_id} (explicitly named and used as the first vertex).")
    txt.append(f"  - Traverse order: Point 0→1→2→3→4→0, enforced to be {COMPUTATION_SETTINGS['traverse_direction']}.")
    txt.append("  - Boundary geometry: straight-line segments connecting successive listed corner coordinates;")
    txt.append("    no natural-feature meanders or adjoiner-based calls are implied.")
    txt.append("  - CRS: EPSG:4326 (WGS 84). Input coordinates are provided as (lat, lon) but geometries are")
    txt.append("    stored as (x=lon, y=lat) per GIS convention to avoid axis-order ambiguity.")
    txt.append("  - Bearings and distances: computed geodesically on the WGS 84 ellipsoid between each")
    txt.append("    consecutive pair of corner coordinates. Bearings are reported as quadrant bearings.")
    txt.append(f"  - Distance units: {COMPUTATION_SETTINGS['distance_units']} (narrative uses U.S. survey feet if selected).")
    txt.append("  - Area: computed as a geodesic polygon area on the ellipsoid; because the corners are digitized,")
    txt.append("    acreage is treated as approximate and rounded to the nearest full acre in the description.")
    txt.append("")
    txt.append("Geometry validation performed:")
    txt.append(f"  - Polygon validity: {poly.is_valid} ({explain_validity(poly) if not poly.is_valid else 'valid'})")
    txt.append(f"  - Vertex count: {len(vertices)}")
    txt.append(f"  - Computed geodesic area: {area_acres:.4f} acres")
    txt.append("")
    txt.append("Known limitations / qualifiers (not resolved by computation):")
    txt.append("  - Corner coordinates are not from a field survey; monument descriptions and survey ties are not provided.")
    txt.append("  - Epoch/date-of-adjustment for WGS 84 realization is not provided; therefore coordinate epoch is listed")
    txt.append("    as NOT PROVIDED.")
    txt.append("")
    txt.append("Files written by this script:")
    txt.append("  - description_of_land.txt (draft description)")
    txt.append("  - boundary_corners.csv and boundary_courses.csv (tabular support)")
    txt.append("  - lease_boundary.shp and lease_points.shp (EPSG:4326)")
    txt.append("  - metadata.json (deliverable metadata and field dictionary)")
    return "\n".join(txt)


def write_shapefiles(out_dir: Path, plss: Dict[str, Any], poly: Polygon, vertices: List[Vertex], signage: List[Dict[str, Any]], COORDINATE_REFERENCE=COORDINATE_REFERENCE) -> None:
    """
    Write:
      - lease_boundary.shp (polygon)
      - lease_points.shp   (points: boundary vertices + signage)
    """
    shp_dir = out_dir
    shp_dir.mkdir(parents=True, exist_ok=True)

    # Polygon shapefile
    poly_attrs = {
        "lease_id": "lease_01",
        "meridian": plss["principal_meridian_name"][:100],
        "state": plss.get("state", "")[:50],
        "twp": f"{plss['township']['number']}{plss['township']['dir']}",
        "rng": f"{plss['range']['number']}{plss['range']['dir']}",
        "sec": int(plss["section"]),
        "subs": ";".join([normalize_subdivision(x) for x in plss.get("subdivisions", [])])[:254],
        "crs": f"EPSG:{COORDINATE_REFERENCE['epsg']}",
        "src": "digitized",
        "acc": "unknown",
    }

    gdf_poly = gpd.GeoDataFrame([poly_attrs], geometry=[poly], crs="EPSG:4326")
    gdf_poly.to_file(shp_dir / "lease_boundary.shp", driver="ESRI Shapefile", index=False)

    # Points shapefile (boundary + signage in one layer)
    point_rows: List[Dict[str, Any]] = []
    point_geoms: List[Point] = []

    # Boundary vertices
    for v in vertices:
        is_pob = "yes" if v.is_pob else "no"
        if v.is_pob:
            feature_key = "lease_boundary_point_of_beginning"
            label = "Point of Beginning"
        else:
            feature_key = f"lease_boundary_point_{v.pt_id}"
            label = f"Boundary Vertex Point {v.pt_id}"

        point_rows.append({
            "feat_key": feature_key[:80],
            "feat_kind": "boundary",
            "pt_id": int(v.pt_id),
            "is_pob": is_pob,
            "label": label[:80],
            "lat": float(v.lat),
            "lon": float(v.lon),
            "sign_id": "",
            "sign_typ": "",
            "status": "",
            "twp": f"{plss['township']['number']}{plss['township']['dir']}",
            "rng": f"{plss['range']['number']}{plss['range']['dir']}",
            "sec": int(plss["section"]),
        })
        point_geoms.append(Point(v.lon, v.lat))

    # Signage points
    for s in signage:
        feature_key = f"signage_{s['id']}"
        point_rows.append({
            "feat_key": feature_key[:80],
            "feat_kind": "signage",
            "pt_id": -1,
            "is_pob": "no",
            "label": s["id"][:80],
            "lat": float(s["lat"]),
            "lon": float(s["lon"]),
            "sign_id": s["id"][:40],
            "sign_typ": str(s.get("type", ""))[:80],
            "status": str(s.get("status", ""))[:20],
            "twp": f"{plss['township']['number']}{plss['township']['dir']}",
            "rng": f"{plss['range']['number']}{plss['range']['dir']}",
            "sec": int(plss["section"]),
        })
        point_geoms.append(Point(float(s["lon"]), float(s["lat"])))

    gdf_pts = gpd.GeoDataFrame(point_rows, geometry=point_geoms, crs="EPSG:4326")
    gdf_pts.to_file(shp_dir / "lease_points.shp", driver="ESRI Shapefile", index=False)


def write_tables(out_dir: Path, vertices: List[Vertex], courses: pd.DataFrame) -> Tuple[Path, Path]:
    corners_rows: List[Dict[str, Any]] = []
    for v in vertices:
        corners_rows.append({
            "pt_id": v.pt_id,
            "is_pob": v.is_pob,
            "lat_dd": v.lat,
            "lon_dd": v.lon,
            "latlon_dms": format_lat_lon_dms(v.lat, v.lon, sec_decimals=2),
        })
    corners_df = pd.DataFrame(corners_rows)

    corners_path = out_dir / "boundary_corners.csv"
    courses_path = out_dir / "boundary_courses.csv"
    corners_df.to_csv(corners_path, index=False)
    courses.to_csv(courses_path, index=False)
    return corners_path, courses_path


def write_metadata(out_dir: Path, plss: Dict[str, Any], COMPUTATION_SETTINGS=COMPUTATION_SETTINGS, COORDINATE_REFERENCE=COORDINATE_REFERENCE, LINEAGE_AND_QUALITY=LINEAGE_AND_QUALITY) -> Path:
    md = {
        "created_utc": utc_now_iso(),
        "coordinate_reference": COORDINATE_REFERENCE,
        "plss": {
            "state": plss.get("state"),
            "principal_meridian": plss.get("principal_meridian_name"),
            "township": plss.get("township"),
            "range": plss.get("range"),
            "section": plss.get("section"),
            "subdivisions_normalized": [normalize_subdivision(x) for x in plss.get("subdivisions", [])],
        },
        "lineage_and_quality": LINEAGE_AND_QUALITY,
        "computation_settings": COMPUTATION_SETTINGS,
        "outputs": {
            "description_file": "description_of_land.txt",
            "choices_file": "choices_and_methods.txt",
            "corners_table": "boundary_corners.csv",
            "courses_table": "boundary_courses.csv",
            "lease_boundary_shapefile": "lease_boundary.shp",
            "lease_points_shapefile": "lease_points.shp",
        },
        "field_dictionary": {
            "lease_boundary.shp": {
                "lease_id": "Identifier for the lease polygon feature",
                "meridian": "Principal meridian name (spelled out)",
                "state": "State name",
                "twp": "Township (e.g., 15S)",
                "rng": "Range (e.g., 1E)",
                "sec": "Section number",
                "subs": "Semicolon-delimited subdivision symbols (normalized)",
                "crs": "CRS label",
                "src": "Source type (digitized/survey/etc.)",
                "acc": "Accuracy statement (coarse)",
            },
            "lease_points.shp": {
                "feat_key": "Unique feature key (e.g., lease_boundary_point_of_beginning, signage_*)",
                "feat_kind": "boundary or signage",
                "pt_id": "Boundary vertex id (0..4) or -1 for signage",
                "is_pob": "yes/no for Point of Beginning",
                "label": "Human-friendly label",
                "lat": "Latitude decimal degrees",
                "lon": "Longitude decimal degrees",
                "sign_id": "Signage id (if feat_kind=signage)",
                "sign_typ": "Sign type string (semicolon-delimited)",
                "status": "existing/proposed",
                "twp": "Township",
                "rng": "Range",
                "sec": "Section",
            },
        },
    }
    p = out_dir / "metadata.json"
    p.write_text(json.dumps(md, indent=2), encoding="utf-8")
    return p


def generate_lease_deliverables(LEASE_PLSS=LEASE_PLSS, 
                                BOUNDARY_VERTICES=BOUNDARY_VERTICES, 
                                SIGNAGE=SIGNAGE, 
                                COMPUTATION_SETTINGS=COMPUTATION_SETTINGS, 
                                COORDINATE_REFERENCE=COORDINATE_REFERENCE, 
                                LINEAGE_AND_QUALITY=LINEAGE_AND_QUALITY,
                                out_dir="output") -> None:

    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)

    vertices = validate_inputs(LEASE_PLSS, BOUNDARY_VERTICES, SIGNAGE)

    # Geodesic computations on WGS 84 ellipsoid
    geod = Geod(ellps="WGS84")

    poly, ordered_vertices = build_polygon(vertices, enforce_clockwise=(COMPUTATION_SETTINGS["traverse_direction"] == "clockwise"))
    courses = compute_courses(ordered_vertices, geod, COMPUTATION_SETTINGS=COMPUTATION_SETTINGS)

    # Area
    area_m2, perim_m = compute_geodesic_area(poly, geod)
    area_acres = acres_from_m2(area_m2)

    # Write description + methods
    description_txt = make_description_of_land(LEASE_PLSS, ordered_vertices, courses, area_acres, COMPUTATION_SETTINGS=COMPUTATION_SETTINGS, COORDINATE_REFERENCE=COORDINATE_REFERENCE, LINEAGE_AND_QUALITY=LINEAGE_AND_QUALITY)
    choices_txt = make_choices_and_methods(ordered_vertices, poly, courses, area_acres, COMPUTATION_SETTINGS=COMPUTATION_SETTINGS)

    (out_dir / "description_of_land.txt").write_text(description_txt, encoding="utf-8")
    (out_dir / "choices_and_methods.txt").write_text(choices_txt, encoding="utf-8")

    # Write tables
    write_tables(out_dir, ordered_vertices, courses)

    # Write shapefiles
    write_shapefiles(out_dir, LEASE_PLSS, poly, ordered_vertices, SIGNAGE, COORDINATE_REFERENCE=COORDINATE_REFERENCE)

    # Metadata
    write_metadata(out_dir, LEASE_PLSS, COMPUTATION_SETTINGS=COMPUTATION_SETTINGS, COORDINATE_REFERENCE=COORDINATE_REFERENCE, LINEAGE_AND_QUALITY=LINEAGE_AND_QUALITY)

    # Console summary
    print(f"Wrote outputs to: {out_dir.resolve()}")
    print(f"Computed geodesic area: {area_acres:.4f} acres")
    print(f"Computed perimeter: {perim_m:.2f} meters")


if __name__ == "__main__":
    generate_lease_deliverables()
