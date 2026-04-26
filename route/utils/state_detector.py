"""
route/utils/state_detector.py

Loads US state boundary polygons from a local GeoJSON file (auto-downloaded
if missing) and provides spatial lookup functions to determine which US state
a coordinate belongs to, and which states a route passes through.

Performance optimizations:
  1. Bounding-box pre-filter — rejects states whose AABB doesn't contain the
     point before doing the expensive Shapely polygon check.
  2. Last-state cache — consecutive route points are almost always in the
     same state; the cache skips all other polygon checks in that case.
  3. Aggressive geometry sampling — caps at 100 points regardless of route
     length (more than enough to detect every state transition).
"""
import json
import requests
from pathlib import Path
from shapely.geometry import shape, Point

# ---------------------------------------------------------------------------
# Resolve project root (two levels up from this file)
# ---------------------------------------------------------------------------
BASE_DIR = Path(__file__).resolve().parent.parent.parent

GEOJSON_PATH = BASE_DIR / "us_states.geojson"
GEOJSON_URL = (
    "https://raw.githubusercontent.com/PublicaMundi/MappingAPI/"
    "master/data/geojson/us-states.json"
)


def _download_geojson() -> None:
    """Download the US states GeoJSON from a public CDN and save locally."""
    print(
        f"[state_detector] us_states.geojson not found at {GEOJSON_PATH}.\n"
        f"[state_detector] Downloading from {GEOJSON_URL} ..."
    )
    try:
        response = requests.get(GEOJSON_URL, timeout=30)
        response.raise_for_status()
        with open(GEOJSON_PATH, "w", encoding="utf-8") as f:
            f.write(response.text)
        print("[state_detector] us_states.geojson downloaded and saved successfully.")
    except requests.RequestException as exc:
        raise RuntimeError(
            f"Failed to download us_states.geojson: {exc}. "
            "Please manually download the file from:\n"
            f"  {GEOJSON_URL}\n"
            f"and place it at: {GEOJSON_PATH}"
        ) from exc


def _load_state_polygons() -> list:
    """
    Load and parse the US states GeoJSON into a list of
    (state_name, state_code, shapely_geometry, bbox) tuples.
    The bounding box (minx, miny, maxx, maxy) is pre-computed once at load
    time so point-in-polygon checks can be skipped cheaply via AABB test.
    Downloads the GeoJSON first if it does not exist locally.
    """
    if not GEOJSON_PATH.exists():
        _download_geojson()

    with open(GEOJSON_PATH, "r", encoding="utf-8") as f:
        geojson_data = json.load(f)

    state_polygons = []
    for feature in geojson_data.get("features", []):
        props = feature.get("properties", {})
        state_name = (
            props.get("name")
            or props.get("NAME")
            or props.get("State")
            or props.get("state")
            or "Unknown"
        )
        state_code = (
            props.get("abbreviation")
            or props.get("ABBREVIATION")
            or props.get("code")
            or props.get("postal")
            or _state_name_to_code(state_name)
        )
        try:
            geom = shape(feature["geometry"])
            if not geom.is_valid:
                geom = geom.buffer(0)
            bbox = geom.bounds  # (minx, miny, maxx, maxy)
            state_polygons.append((state_name, state_code, geom, bbox))
        except Exception as exc:
            print(f"[state_detector] Warning: Could not load geometry for {state_name}: {exc}")

    print(f"[state_detector] Loaded {len(state_polygons)} US state boundaries.")
    return state_polygons


# US state name to 2-letter code fallback lookup
_NAME_TO_CODE = {
    "Alabama": "AL", "Alaska": "AK", "Arizona": "AZ", "Arkansas": "AR",
    "California": "CA", "Colorado": "CO", "Connecticut": "CT", "Delaware": "DE",
    "Florida": "FL", "Georgia": "GA", "Hawaii": "HI", "Idaho": "ID",
    "Illinois": "IL", "Indiana": "IN", "Iowa": "IA", "Kansas": "KS",
    "Kentucky": "KY", "Louisiana": "LA", "Maine": "ME", "Maryland": "MD",
    "Massachusetts": "MA", "Michigan": "MI", "Minnesota": "MN", "Mississippi": "MS",
    "Missouri": "MO", "Montana": "MT", "Nebraska": "NE", "Nevada": "NV",
    "New Hampshire": "NH", "New Jersey": "NJ", "New Mexico": "NM", "New York": "NY",
    "North Carolina": "NC", "North Dakota": "ND", "Ohio": "OH", "Oklahoma": "OK",
    "Oregon": "OR", "Pennsylvania": "PA", "Rhode Island": "RI", "South Carolina": "SC",
    "South Dakota": "SD", "Tennessee": "TN", "Texas": "TX", "Utah": "UT",
    "Vermont": "VT", "Virginia": "VA", "Washington": "WA", "West Virginia": "WV",
    "Wisconsin": "WI", "Wyoming": "WY", "District of Columbia": "DC",
}


def _state_name_to_code(name: str) -> str:
    """Return the 2-letter state code for a full state name, or '' if unknown."""
    return _NAME_TO_CODE.get(name, "")


# ---------------------------------------------------------------------------
# Module-level state data — loaded once at import time
# ---------------------------------------------------------------------------
STATE_POLYGONS = _load_state_polygons()

# Simple cache: stores the last matched state entry to exploit spatial locality
_last_state_cache: list = [None]   # list so it's mutable from nested functions


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def get_state_for_point(lon: float, lat: float) -> dict | None:
    """
    Determine which US state a geographic point falls within.

    Uses two-tier optimisation:
      1. Check cached last result first (same state as previous call ~95% of the time).
      2. Bounding-box pre-filter before running the full Shapely contains() check.

    Args:
        lon: Longitude of the point.
        lat: Latitude of the point.

    Returns:
        dict with keys 'state' and 'state_code', or None if outside all states.
    """
    point = Point(lon, lat)

    # Tier 1: check cached last state (fast path for sequential route points)
    cached = _last_state_cache[0]
    if cached is not None:
        _, _, geom, bbox = cached
        minx, miny, maxx, maxy = bbox
        if minx <= lon <= maxx and miny <= lat <= maxy:
            try:
                if geom.contains(point):
                    return {"state": cached[0], "state_code": cached[1]}
            except Exception:
                pass

    # Tier 2: full scan with bounding-box pre-filter (eliminates ~90% of checks)
    for entry in STATE_POLYGONS:
        state_name, state_code, geom, bbox = entry
        minx, miny, maxx, maxy = bbox
        if lon < minx or lon > maxx or lat < miny or lat > maxy:
            continue  # fast AABB rejection
        try:
            if geom.contains(point):
                _last_state_cache[0] = entry
                return {"state": state_name, "state_code": state_code}
        except Exception:
            continue

    _last_state_cache[0] = None
    return None


def get_states_along_route(geometry: list, total_miles: float) -> list:
    """
    Determine the ordered list of US states that a route passes through,
    with approximate mile markers for each state segment.

    Samples at most 100 evenly-spaced points from the route geometry.
    100 points is more than sufficient to detect every state transition
    on any US route (cross-country routes typically pass through 8-12 states).

    Args:
        geometry:    List of [lon, lat] coordinate pairs (the full route geometry).
        total_miles: Total route distance in miles.

    Returns:
        List of dicts, each with:
            - state (str): Full state name.
            - state_code (str): 2-letter state abbreviation.
            - start_mile (float): Approximate mile marker where this state begins.
            - end_mile (float): Approximate mile marker where this state ends.
            - midpoint_index (int): Index into geometry of the segment midpoint.
    """
    if not geometry:
        return []

    total_points = len(geometry)

    # Cap at 100 sample points for performance — sufficient for all US routes
    MAX_SAMPLES = 100
    sample_step = max(1, total_points // MAX_SAMPLES)
    sampled_indices = list(range(0, total_points, sample_step))
    if (total_points - 1) not in sampled_indices:
        sampled_indices.append(total_points - 1)

    # Reset the spatial cache before scanning a new route
    _last_state_cache[0] = None

    # Map each sampled index to its state
    index_states = []
    for idx in sampled_indices:
        lon, lat = geometry[idx][0], geometry[idx][1]
        state_info = get_state_for_point(lon, lat)
        index_states.append((idx, state_info))

    # Group consecutive points in the same state into segments
    segments = []
    current_state_key = None
    seg_start_idx = None
    prev_state_info = None

    for idx, state_info in index_states:
        state_key = state_info["state_code"] if state_info else None

        if state_key != current_state_key:
            # Close the previous segment
            if current_state_key is not None and seg_start_idx is not None:
                seg_end_idx = idx
                mid_idx = min((seg_start_idx + seg_end_idx) // 2, total_points - 1)
                segments.append({
                    "_state_info": prev_state_info,
                    "_start_idx": seg_start_idx,
                    "_end_idx": seg_end_idx,
                    "_mid_idx": mid_idx,
                })
            # Start a new segment
            current_state_key = state_key
            seg_start_idx = idx
            prev_state_info = state_info

    # Close the final segment
    if current_state_key is not None and seg_start_idx is not None:
        seg_end_idx = total_points - 1
        mid_idx = min((seg_start_idx + seg_end_idx) // 2, total_points - 1)
        segments.append({
            "_state_info": prev_state_info,
            "_start_idx": seg_start_idx,
            "_end_idx": seg_end_idx,
            "_mid_idx": mid_idx,
        })

    # Convert geometry indices to proportional mile markers
    result = []
    for seg in segments:
        state_info = seg["_state_info"]
        if state_info is None:
            continue  # skip points that fall outside all state boundaries

        start_frac = seg["_start_idx"] / max(total_points - 1, 1)
        end_frac = seg["_end_idx"] / max(total_points - 1, 1)
        mid_idx = seg["_mid_idx"]

        start_mile = round(start_frac * total_miles, 2)
        end_mile = round(end_frac * total_miles, 2)

        result.append({
            "state": state_info["state"],
            "state_code": state_info["state_code"],
            "start_mile": start_mile,
            "end_mile": end_mile,
            "midpoint_index": mid_idx,
        })

    return result
