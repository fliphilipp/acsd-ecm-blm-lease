# +
# %matplotlib widget
# %load_ext autoreload
# %autoreload 2

from typing import Any, Dict, List
from utils import generate_lease_deliverables
# -

# # Inputs

# +
LEASE_PLSS: Dict[str, Any] = {
    "state": "California",
    "principal_meridian_name": "San Bernardino Meridian",
    "township": {"number": 15, "dir": "S"},  # "15s" -> 15 S
    "range": {"number": 1, "dir": "E"},      # "1e"  -> 1 E
    "section": 1,
    "subdivisions": [ # accepts either "NE1/4 of NE1/4" or "NE1/4NE1/4" and then normalizes
        "NE1/4 of NE1/4",
        "SE1/4 of NE1/4",
        "NE1/4 of SE1/4",
    ],
}

# EPSG:4326 in input lat, lon order (as provided)
# is_pob specified Point Of Beginning
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
    "epoch_or_adjustment_date": "NOT PROVIDED",  # not provided, keep this explicit
    "geoid_model": "N/A (no elevations used)",   # no elevations used, geoid is not applicable for horizontal-only output
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

# Computation/format defaults (can adjust these to preference)
COMPUTATION_SETTINGS: Dict[str, Any] = {
    "traverse_direction": "clockwise",      # "clockwise" is standard
    "distance_units": "us_survey_feet",     # could make "meters"
    "distance_decimals": 2,                 # formatting only; does not imply survey-grade accuracy
    "bearing_seconds_decimals": 2,          # formatting for metes-and-bounds
    "area_rounding": "nearest_full_acre",   # for approximate area clause
}

# +
kwargs = {
    "LEASE_PLSS":            LEASE_PLSS,
    "BOUNDARY_VERTICES":     BOUNDARY_VERTICES,
    "SIGNAGE":               SIGNAGE,
    "COORDINATE_REFERENCE":  COORDINATE_REFERENCE,
    "LINEAGE_AND_QUALITY":   LINEAGE_AND_QUALITY, 
    "COMPUTATION_SETTINGS":  COMPUTATION_SETTINGS,
}

generate_lease_deliverables(**kwargs)
# -


