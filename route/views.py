"""
route/views.py

Django REST Framework API views for the Fuel Route Optimizer.

Endpoints:
    POST /api/route/             — Calculate optimal fuel stop route
    GET  /api/route/map/<id>/   — Retrieve the generated interactive HTML map
    GET  /api/health/           — Health check
"""
import uuid
import sys
import traceback

import pandas as pd
from pathlib import Path
from django.conf import settings
from django.http import HttpResponse
from rest_framework.views import APIView
from rest_framework.response import Response
from rest_framework import status

from .utils.geocoder import geocode
from .utils.router import get_route
from .utils.fuel_optimizer import optimize_fuel_stops
from .utils.map_generator import generate_map

# ---------------------------------------------------------------------------
# Load fuel_prices.csv at module startup (once, not per request)
# ---------------------------------------------------------------------------
BASE_DIR = settings.BASE_DIR
_CSV_PATH = BASE_DIR / "fuel_prices.csv"

try:
    fuel_prices_df = pd.read_csv(_CSV_PATH)
    print("\n[views] ✅ fuel_prices.csv loaded successfully.")
    print(f"[views]    Path: {_CSV_PATH}")
    print(f"[views]    Shape: {fuel_prices_df.shape[0]} rows × {fuel_prices_df.shape[1]} columns")
    print(f"[views]    Detected columns: {list(fuel_prices_df.columns)}\n")
except FileNotFoundError:
    fuel_prices_df = None
    print(
        "\n[views] ❌ ERROR: fuel_prices.csv not found.\n"
        f"[views]    Expected location: {_CSV_PATH}\n"
        "[views]    Please place the company-provided fuel_prices.csv in the project root.\n",
        file=sys.stderr,
    )
except Exception as _csv_exc:
    fuel_prices_df = None
    print(
        f"\n[views] ❌ ERROR loading fuel_prices.csv: {_csv_exc}\n",
        file=sys.stderr,
    )

# ---------------------------------------------------------------------------
# In-memory map storage: route_id → HTML string
# ---------------------------------------------------------------------------
MAP_STORE: dict[str, str] = {}


# ---------------------------------------------------------------------------
# Helper
# ---------------------------------------------------------------------------

def _csv_error_response():
    """Return a 500 response indicating that fuel_prices.csv could not be loaded."""
    return Response(
        {
            "error": (
                "fuel_prices.csv not found or could not be loaded. "
                "Please place the company-provided file in the project root."
            )
        },
        status=status.HTTP_500_INTERNAL_SERVER_ERROR,
    )


# ---------------------------------------------------------------------------
# Views
# ---------------------------------------------------------------------------

class RouteView(APIView):
    """
    POST /api/route/

    Body (JSON):
        {
            "start":  "New York, NY",
            "finish": "Los Angeles, CA"
        }

    Returns a JSON payload with:
        - Route summary (distance, cost, gallons)
        - Ordered list of fuel stops
        - URL to view the interactive map
    """

    def post(self, request):
        # --- 1. Validate request body ---
        start_input = request.data.get("start", "")
        finish_input = request.data.get("finish", "")

        if not start_input or not isinstance(start_input, str) or not start_input.strip():
            return Response(
                {"error": "Missing or empty 'start' field. Please provide a starting location."},
                status=status.HTTP_400_BAD_REQUEST,
            )

        if not finish_input or not isinstance(finish_input, str) or not finish_input.strip():
            return Response(
                {"error": "Missing or empty 'finish' field. Please provide a destination location."},
                status=status.HTTP_400_BAD_REQUEST,
            )

        start_input = start_input.strip()
        finish_input = finish_input.strip()

        # --- 2. Guard: CSV must be loaded ---
        if fuel_prices_df is None:
            return _csv_error_response()

        # --- 3. Geocode start location ---
        try:
            start_geo = geocode(start_input)
        except PermissionError as exc:
            return Response({"error": str(exc)}, status=status.HTTP_502_BAD_GATEWAY)
        except ConnectionError as exc:
            return Response({"error": str(exc)}, status=status.HTTP_502_BAD_GATEWAY)
        except ValueError as exc:
            return Response({"error": str(exc)}, status=status.HTTP_400_BAD_REQUEST)
        except Exception as exc:
            return Response(
                {"error": f"Unexpected error geocoding start location: {exc}"},
                status=status.HTTP_500_INTERNAL_SERVER_ERROR,
            )

        # --- 4. Geocode finish location ---
        try:
            finish_geo = geocode(finish_input)
        except PermissionError as exc:
            return Response({"error": str(exc)}, status=status.HTTP_502_BAD_GATEWAY)
        except ConnectionError as exc:
            return Response({"error": str(exc)}, status=status.HTTP_502_BAD_GATEWAY)
        except ValueError as exc:
            return Response({"error": str(exc)}, status=status.HTTP_400_BAD_REQUEST)
        except Exception as exc:
            return Response(
                {"error": f"Unexpected error geocoding finish location: {exc}"},
                status=status.HTTP_500_INTERNAL_SERVER_ERROR,
            )

        # --- 5. Get driving route ---
        # ORS expects (lon, lat) order
        start_coords_ors = (start_geo["lon"], start_geo["lat"])
        finish_coords_ors = (finish_geo["lon"], finish_geo["lat"])

        try:
            route_data = get_route(start_coords_ors, finish_coords_ors)
        except PermissionError as exc:
            return Response({"error": str(exc)}, status=status.HTTP_502_BAD_GATEWAY)
        except ConnectionError as exc:
            return Response({"error": str(exc)}, status=status.HTTP_502_BAD_GATEWAY)
        except ValueError as exc:
            return Response({"error": str(exc)}, status=status.HTTP_400_BAD_REQUEST)
        except Exception as exc:
            return Response(
                {"error": f"Unexpected error calculating route: {exc}"},
                status=status.HTTP_500_INTERNAL_SERVER_ERROR,
            )

        # --- 6. Optimize fuel stops ---
        try:
            fuel_stops = optimize_fuel_stops(route_data, fuel_prices_df)
        except Exception as exc:
            return Response(
                {"error": f"Unexpected error optimizing fuel stops: {exc}"},
                status=status.HTTP_500_INTERNAL_SERVER_ERROR,
            )

        # --- 7. Calculate totals ---
        total_distance_miles = route_data["distance_miles"]
        total_gallons_needed = total_distance_miles / 10  # 10 MPG
        total_fuel_cost_usd = sum(
            stop["cost_at_this_stop"]
            for stop in fuel_stops
            if stop.get("cost_at_this_stop") is not None
        )

        # --- 8. Generate interactive map ---
        # Folium uses (lat, lon) order for marker locations
        start_latlon = (start_geo["lat"], start_geo["lon"])
        finish_latlon = (finish_geo["lat"], finish_geo["lon"])

        try:
            map_html = generate_map(
                start_coords=start_latlon,
                end_coords=finish_latlon,
                start_name=start_geo["display_name"],
                end_name=finish_geo["display_name"],
                route_geometry=route_data["geometry"],
                fuel_stops=fuel_stops,
            )
        except Exception as exc:
            return Response(
                {"error": f"Unexpected error generating map: {exc}"},
                status=status.HTTP_500_INTERNAL_SERVER_ERROR,
            )

        # --- 9. Store map and generate route ID ---
        route_id = str(uuid.uuid4())
        MAP_STORE[route_id] = map_html

        # --- 10. Build and return response ---
        return Response(
            {
                "start": start_geo["display_name"],
                "finish": finish_geo["display_name"],
                "total_distance_miles": round(total_distance_miles, 2),
                "total_gallons_needed": round(total_gallons_needed, 2),
                "total_fuel_cost_usd": round(total_fuel_cost_usd, 2),
                "fuel_stops": fuel_stops,
                "map_url": f"/api/route/map/{route_id}/",
                "optimization_note": (
                    "Fuel stops selected to minimize cost by choosing the "
                    "lowest-price state within each 500-mile range window."
                ),
            },
            status=status.HTTP_200_OK,
        )


class MapView(APIView):
    """
    GET /api/route/map/<route_id>/

    Returns the pre-generated Folium interactive map as a standalone HTML page.
    """

    def get(self, request, route_id: str):
        html = MAP_STORE.get(route_id)

        if html is None:
            return Response(
                {
                    "error": (
                        f"Map with ID '{route_id}' not found. "
                        "Maps are stored in memory and are lost on server restart. "
                        "Please make a new POST /api/route/ request."
                    )
                },
                status=status.HTTP_404_NOT_FOUND,
            )

        return HttpResponse(html, content_type="text/html; charset=utf-8")


class HealthView(APIView):
    """
    GET /api/health/

    Returns a simple status check to confirm the API is running.
    """

    def get(self, request):
        csv_status = "loaded" if fuel_prices_df is not None else "MISSING — place fuel_prices.csv in project root"
        return Response(
            {
                "status": "ok",
                "message": "Fuel Route API is running",
                "fuel_prices_csv": csv_status,
            },
            status=status.HTTP_200_OK,
        )
