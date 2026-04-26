"""
route/utils/geocoder.py

Geocodes a human-readable US location string into (lat, lon) coordinates
using the OpenRouteService Geocoding API.
"""
import os
import requests
from django.conf import settings


def geocode(location_name: str) -> dict:
    """
    Convert a location name string to geographic coordinates using ORS geocoding.

    Args:
        location_name: Human-readable place name, e.g. "New York, NY"

    Returns:
        dict with keys:
            - lat (float): Latitude
            - lon (float): Longitude
            - display_name (str): Full display name returned by the API

    Raises:
        ValueError: If the location cannot be found or is outside the USA.
        ConnectionError: If the ORS API is unreachable.
        PermissionError: If the API key is invalid (HTTP 403).
    """
    api_key = settings.ORS_API_KEY
    if not api_key or api_key == 'your_openrouteservice_api_key_here':
        raise PermissionError(
            "ORS_API_KEY is not configured. Please add your key to the .env file."
        )

    url = "https://api.openrouteservice.org/geocode/search"
    params = {
        "api_key": api_key,
        "text": location_name,
        "boundary.country": "US",
        "size": 1,
    }

    try:
        response = requests.get(url, params=params, timeout=15)
    except requests.exceptions.ConnectionError:
        raise ConnectionError(
            "Unable to reach OpenRouteService API. Check your internet connection."
        )
    except requests.exceptions.Timeout:
        raise ConnectionError(
            "OpenRouteService API request timed out. Please try again."
        )

    if response.status_code == 403:
        raise PermissionError(
            "Invalid ORS API key. Please check your ORS_API_KEY in the .env file."
        )

    if response.status_code == 429:
        raise ConnectionError(
            "ORS API rate limit exceeded. Please wait a moment and try again."
        )

    if not response.ok:
        raise ConnectionError(
            f"ORS Geocoding API returned HTTP {response.status_code}: {response.text[:200]}"
        )

    try:
        data = response.json()
    except ValueError:
        raise ConnectionError(
            "ORS Geocoding API returned an unexpected non-JSON response."
        )

    features = data.get("features", [])
    if not features:
        raise ValueError(
            f"Location not found or is outside the USA: '{location_name}'. "
            "Please provide a valid US city, state, or address."
        )

    feature = features[0]
    coordinates = feature.get("geometry", {}).get("coordinates", [])
    if len(coordinates) < 2:
        raise ValueError(
            f"Geocoding returned an invalid coordinate for '{location_name}'."
        )

    lon = float(coordinates[0])
    lat = float(coordinates[1])
    display_name = feature.get("properties", {}).get("label", location_name)

    # Verify the result is geographically within the continental USA bounding box
    # (including Alaska and Hawaii with a loose boundary)
    if not (-180.0 <= lon <= -60.0 and 15.0 <= lat <= 72.0):
        raise ValueError(
            f"The location '{location_name}' resolved to coordinates outside the USA "
            f"(lat={lat:.4f}, lon={lon:.4f}). Please enter a valid US location."
        )

    return {
        "lat": lat,
        "lon": lon,
        "display_name": display_name,
    }
