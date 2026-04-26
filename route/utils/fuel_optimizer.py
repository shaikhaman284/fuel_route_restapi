"""
route/utils/fuel_optimizer.py

Calculates the optimal fuel stop plan for a route, choosing stops in the
lowest-price states reachable within the vehicle's 500-mile fuel range.
"""
import pandas as pd
from .state_detector import get_states_along_route

# Vehicle constants
TANK_RANGE_MILES = 500       # Maximum range on a full tank
MPG = 10                     # Miles per gallon (gallons = miles / 10)


def _find_price_for_state(state_code: str, fuel_prices_df: pd.DataFrame) -> float | None:
    """
    Look up the median fuel price for a given state code in the DataFrame.

    Handles flexible column naming for both the state and price columns.
    Uses the median across all rows for that state to get a reliable average
    (the CSV typically has many rows per state — one per truck stop).

    Returns the median price as a float, or None if not found.
    """
    df = fuel_prices_df.copy()
    columns_lower = {col.lower().strip(): col for col in df.columns}

    # --- Identify the state identifier column ---
    state_col = None
    for candidate in ["state_code", "statecode", "abbreviation", "code",
                       "postal", "state_abbreviation", "state"]:
        if candidate in columns_lower:
            state_col = columns_lower[candidate]
            break

    if state_col is None:
        return None

    # --- Identify the price column ---
    # NOTE: includes 'retail price' and 'retail_price' for the provided CSV format
    price_col = None
    for candidate in [
        "retail price", "retail_price",
        "price_per_gallon", "price", "fuel_price", "cost",
        "avg_price", "average_price", "gas_price", "cost_per_gallon",
    ]:
        if candidate in columns_lower:
            price_col = columns_lower[candidate]
            break

    if price_col is None:
        return None

    # --- Look up the state (normalise: strip + uppercase) ---
    df["_lookup_key"] = df[state_col].astype(str).str.strip().str.upper()
    match = df[df["_lookup_key"] == state_code.strip().upper()]

    # If no match on abbreviation, try matching against a full-name column
    if match.empty:
        for candidate in ["state_name", "name", "full_name"]:
            if candidate in columns_lower:
                alt_col = columns_lower[candidate]
                if alt_col != state_col:
                    df["_alt_key"] = df[alt_col].astype(str).str.strip().str.upper()
                    match = df[df["_alt_key"] == state_code.strip().upper()]
                    if not match.empty:
                        break

    if match.empty:
        return None

    try:
        # Use median to get a representative price across all truck stops in the state
        prices = pd.to_numeric(match[price_col], errors="coerce").dropna()
        if prices.empty:
            return None
        return float(prices.median())
    except (ValueError, TypeError):
        return None


def optimize_fuel_stops(route_data: dict, fuel_prices_df: pd.DataFrame) -> list:
    """
    Compute the optimal sequence of fuel stops along a route.

    Strategy:
      - Vehicle starts with a full tank (500-mile range).
      - At each decision point, look ahead up to the remaining range.
      - Among all reachable states with known prices, pick the cheapest one.
      - Place the stop at the midpoint of that state's route segment.
      - Reset to a full tank after each stop.
      - Repeat until the destination is within remaining range.

    Args:
        route_data: Dict returned by router.get_route(), containing:
                    - distance_miles (float)
                    - geometry (list of [lon, lat])
        fuel_prices_df: pandas DataFrame loaded from fuel_prices.csv.

    Returns:
        List of stop dicts, each containing:
            stop_number, state, state_code, lat, lon,
            distance_from_start_miles, distance_from_previous_stop_miles,
            price_per_gallon, gallons_purchased, cost_at_this_stop
    """
    geometry = route_data["geometry"]
    total_miles = route_data["distance_miles"]

    # Get ordered state segments with mile markers
    state_segments = get_states_along_route(geometry, total_miles)

    if not state_segments:
        return []

    stops = []
    current_mile = 0.0
    remaining_range = TANK_RANGE_MILES
    stop_number = 1
    previous_stop_mile = 0.0
    
    price_cache = {}

    while True:
        # Check if destination is already within remaining range
        destination_distance = total_miles - current_mile
        if destination_distance <= remaining_range:
            # We can reach the destination without stopping
            break

        # Find all state segments that are reachable within remaining_range
        # A segment is reachable if its START mile is within current range
        reachable_segments = []
        for seg in state_segments:
            seg_start = seg["start_mile"]
            seg_end = seg["end_mile"]

            mid_mile = (seg_start + seg_end) / 2

            if mid_mile <= current_mile:
                continue  # already passed the midpoint of this state

            if mid_mile > current_mile + remaining_range:
                continue  # midpoint is out of reach

            state_code = seg["state_code"]
            if state_code not in price_cache:
                price = _find_price_for_state(state_code, fuel_prices_df)
                if price is None:
                    # Also try matching by full state name
                    price = _find_price_for_state(seg["state"], fuel_prices_df)
                price_cache[state_code] = price
            
            price = price_cache[state_code]

            if price is None:
                continue  # Skip states without price data

            reachable_segments.append({
                "state": seg["state"],
                "state_code": seg["state_code"],
                "mid_mile": mid_mile,
                "midpoint_index": seg["midpoint_index"],
                "price_per_gallon": price,
                "original_seg": seg,
            })

        if not reachable_segments:
            # No state with known price is reachable — emergency: stop at max range
            emergency_mile = current_mile + remaining_range - 1
            # Find which state segment contains this emergency_mile
            seg = None
            for s in state_segments:
                if s["start_mile"] <= emergency_mile <= s["end_mile"]:
                    seg = s
                    break
            if not seg:
                fallback_segs = [s for s in state_segments if s["end_mile"] > current_mile]
                if fallback_segs:
                    seg = fallback_segs[-1]

            if seg:
                stop_mile = emergency_mile
                mid_idx = seg["midpoint_index"]
                lon = geometry[mid_idx][0]
                lat = geometry[mid_idx][1]
                miles_driven = stop_mile - previous_stop_mile
                gallons = miles_driven / MPG
                stops.append({
                    "stop_number": stop_number,
                    "state": seg["state"],
                    "state_code": seg["state_code"],
                    "lat": round(lat, 6),
                    "lon": round(lon, 6),
                    "distance_from_start_miles": round(stop_mile, 2),
                    "distance_from_previous_stop_miles": round(miles_driven, 2),
                    "price_per_gallon": None,
                    "gallons_purchased": round(gallons, 3),
                    "cost_at_this_stop": None,
                    "note": "No price data available; stop placed for range safety.",
                })
                stop_number += 1
                previous_stop_mile = stop_mile
                current_mile = stop_mile
                remaining_range = TANK_RANGE_MILES
                continue
            else:
                break  # Cannot continue

        # Pick the segment with the lowest fuel price
        best_seg = min(reachable_segments, key=lambda s: s["price_per_gallon"])

        # Place the stop at the midpoint of the best segment's reachable portion
        stop_mile = best_seg["mid_mile"]
        stop_mile = round(stop_mile, 2)
        
        # Safeguard against infinite loops due to tiny segments or rounding
        if stop_mile <= current_mile:
            stop_mile = current_mile + 0.01

        # Get coordinates for the midpoint of the best segment
        mid_idx = best_seg["midpoint_index"]
        # Clamp index to geometry length
        mid_idx = min(mid_idx, len(geometry) - 1)
        lon = geometry[mid_idx][0]
        lat = geometry[mid_idx][1]

        # Calculate cost metrics
        miles_driven_since_last_stop = stop_mile - previous_stop_mile
        gallons_purchased = miles_driven_since_last_stop / MPG
        cost_at_this_stop = gallons_purchased * best_seg["price_per_gallon"]

        stops.append({
            "stop_number": stop_number,
            "state": best_seg["state"],
            "state_code": best_seg["state_code"],
            "lat": round(lat, 6),
            "lon": round(lon, 6),
            "distance_from_start_miles": round(stop_mile, 2),
            "distance_from_previous_stop_miles": round(miles_driven_since_last_stop, 2),
            "price_per_gallon": round(best_seg["price_per_gallon"], 4),
            "gallons_purchased": round(gallons_purchased, 3),
            "cost_at_this_stop": round(cost_at_this_stop, 2),
        })

        # Advance position
        previous_stop_mile = stop_mile
        current_mile = stop_mile
        remaining_range = TANK_RANGE_MILES - (stop_mile - current_mile)
        # After a full refuel, remaining range resets to full tank minus
        # any distance already driven from this stop (which is zero here)
        remaining_range = TANK_RANGE_MILES
        stop_number += 1

    return stops
