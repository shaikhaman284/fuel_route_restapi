"""
route/utils/router.py

Fetches a driving route between two coordinate pairs using the
OpenRouteService Directions API (GeoJSON format).
"""
import requests
from django.conf import settings


def get_route(start_coords: tuple, end_coords: tuple) -> dict:
    """
    Retrieve a driving route between two locations using ORS Directions API.

    Args:
        start_coords: (lon, lat) tuple for the start point.
                      NOTE: ORS uses longitude-first ordering.
        end_coords:   (lon, lat) tuple for the destination.

    Returns:
        dict with keys:
            - distance_meters (float): Total route distance in meters.
            - distance_miles  (float): Total route distance in miles.
            - duration_seconds (float): Estimated driving duration in seconds.
            - geometry (list): List of [lon, lat] coordinate pairs along the route.

    Raises:
        ValueError: If ORS cannot calculate a route between the two points.
        ConnectionError: If the ORS API is unreachable or returns an error.
        PermissionError: If the API key is invalid.
    """
    api_key = settings.ORS_API_KEY
    if not api_key or api_key == 'your_openrouteservice_api_key_here':
        raise PermissionError(
            "ORS_API_KEY is not configured. Please add your key to the .env file."
        )

    url = "https://api.openrouteservice.org/v2/directions/driving-car/geojson"
    headers = {
        "Authorization": api_key,
        "Content-Type": "application/json",
        "Accept": "application/json, application/geo+json",
    }
    body = {
        "coordinates": [
            [start_coords[0], start_coords[1]],
            [end_coords[0], end_coords[1]],
        ]
    }

    try:
        response = requests.post(url, json=body, headers=headers, timeout=30)
    except requests.exceptions.ConnectionError:
        raise ConnectionError(
            "Unable to reach OpenRouteService API. Check your internet connection."
        )
    except requests.exceptions.Timeout:
        raise ConnectionError(
            "OpenRouteService Directions API request timed out. Please try again."
        )

    if response.status_code == 403:
        raise PermissionError(
            "Invalid ORS API key. Please check your ORS_API_KEY in the .env file."
        )

    if response.status_code == 429:
        raise ConnectionError(
            "ORS API rate limit exceeded. Please wait a moment and try again."
        )

    if response.status_code == 404:
        raise ValueError(
            "ORS could not find a drivable route between the two locations. "
            "Ensure both locations are accessible by road within the USA."
        )

    if not response.ok:
        # Try to extract a helpful error message from the ORS response body
        try:
            error_detail = response.json()
            ors_message = (
                error_detail.get("error", {}).get("message", "")
                or str(error_detail)
            )
        except ValueError:
            ors_message = response.text[:300]
        raise ConnectionError(
            f"ORS Directions API returned HTTP {response.status_code}: {ors_message}"
        )

    try:
        data = response.json()
    except ValueError:
        raise ConnectionError(
            "ORS Directions API returned an unexpected non-JSON response."
        )

    features = data.get("features", [])
    if not features:
        raise ValueError(
            "ORS returned an empty route. Cannot calculate fuel stops."
        )

    feature = features[0]
    properties = feature.get("properties", {})
    summary = properties.get("summary", {})

    distance_meters = float(summary.get("distance", 0))
    duration_seconds = float(summary.get("duration", 0))
    distance_miles = distance_meters / 1609.344

    geometry_coords = feature.get("geometry", {}).get("coordinates", [])
    if not geometry_coords:
        raise ValueError(
            "ORS returned a route with no geometry coordinates."
        )

    return {
        "distance_meters": distance_meters,
        "distance_miles": round(distance_miles, 4),
        "duration_seconds": duration_seconds,
        "geometry": geometry_coords,  # list of [lon, lat]
    }
