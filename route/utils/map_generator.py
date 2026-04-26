"""
route/utils/map_generator.py

Generates an interactive HTML map of the route and fuel stops using Folium.
No external API call is needed — Folium renders maps using Leaflet.js with
OpenStreetMap tiles.
"""
import folium


def generate_map(
    start_coords: tuple,
    end_coords: tuple,
    start_name: str,
    end_name: str,
    route_geometry: list,
    fuel_stops: list,
) -> str:
    """
    Build a full interactive HTML map of the route with fuel stop markers.

    Args:
        start_coords:    (lat, lon) of the start point.
        end_coords:      (lat, lon) of the end point.
        start_name:      Display name of the start location.
        end_name:        Display name of the end location.
        route_geometry:  List of [lon, lat] coordinate pairs (ORS format).
        fuel_stops:      List of stop dicts from fuel_optimizer.optimize_fuel_stops().

    Returns:
        A complete standalone HTML string representing the Folium map.
    """
    # Convert route geometry from [lon, lat] to [lat, lon] for Folium/Leaflet
    route_latlon = [[coord[1], coord[0]] for coord in route_geometry]

    # Calculate geographic center of the route for initial map view
    all_lats = [c[0] for c in route_latlon]
    all_lons = [c[1] for c in route_latlon]
    center_lat = (min(all_lats) + max(all_lats)) / 2
    center_lon = (min(all_lons) + max(all_lons)) / 2

    # Create Folium map
    fmap = folium.Map(
        location=[center_lat, center_lon],
        zoom_start=5,
        tiles="OpenStreetMap",
    )

    # Auto-fit the map to the route bounds
    sw = [min(all_lats), min(all_lons)]
    ne = [max(all_lats), max(all_lons)]
    fmap.fit_bounds([sw, ne], padding=(30, 30))

    # Draw the full route as a blue polyline
    folium.PolyLine(
        locations=route_latlon,
        color="#1A73E8",
        weight=4,
        opacity=0.8,
        tooltip="Route",
    ).add_to(fmap)

    # --- Start marker (green) ---
    folium.Marker(
        location=[start_coords[0], start_coords[1]],
        popup=folium.Popup(
            f"<b>🟢 Start</b><br>{start_name}",
            max_width=250,
        ),
        tooltip=f"Start: {start_name}",
        icon=folium.Icon(color="green", icon="play", prefix="fa"),
    ).add_to(fmap)

    # --- End marker (red) ---
    folium.Marker(
        location=[end_coords[0], end_coords[1]],
        popup=folium.Popup(
            f"<b>🔴 Finish</b><br>{end_name}",
            max_width=250,
        ),
        tooltip=f"Finish: {end_name}",
        icon=folium.Icon(color="red", icon="flag-checkered", prefix="fa"),
    ).add_to(fmap)

    # --- Fuel stop markers (orange) ---
    for stop in fuel_stops:
        price_str = (
            f"${stop['price_per_gallon']:.3f}/gal"
            if stop.get("price_per_gallon") is not None
            else "N/A"
        )
        cost_str = (
            f"${stop['cost_at_this_stop']:.2f}"
            if stop.get("cost_at_this_stop") is not None
            else "N/A"
        )
        note_html = ""
        if stop.get("note"):
            note_html = f"<br><i style='color:gray;font-size:11px;'>{stop['note']}</i>"

        popup_html = (
            f"<div style='font-family:Arial,sans-serif;min-width:180px;'>"
            f"<b>⛽ Stop #{stop['stop_number']}</b><br>"
            f"<b>State:</b> {stop['state']} ({stop['state_code']})<br>"
            f"<b>Mile Marker:</b> {stop['distance_from_start_miles']:.1f} mi<br>"
            f"<b>Price:</b> {price_str}<br>"
            f"<b>Gallons:</b> {stop['gallons_purchased']:.2f}<br>"
            f"<b>Cost:</b> {cost_str}"
            f"{note_html}"
            f"</div>"
        )

        folium.Marker(
            location=[stop["lat"], stop["lon"]],
            popup=folium.Popup(popup_html, max_width=280),
            tooltip=f"Stop #{stop['stop_number']} — {stop['state']} ({price_str})",
            icon=folium.Icon(color="orange", icon="gas-pump", prefix="fa"),
        ).add_to(fmap)

    # --- HTML Legend (bottom-right) ---
    legend_html = """
    <div style="
        position: fixed;
        bottom: 30px;
        right: 30px;
        z-index: 9999;
        background-color: white;
        padding: 12px 16px;
        border-radius: 8px;
        border: 2px solid #ccc;
        box-shadow: 2px 2px 8px rgba(0,0,0,0.2);
        font-family: Arial, sans-serif;
        font-size: 13px;
        line-height: 1.8;
    ">
        <b style="font-size:14px;">Map Legend</b><br>
        <span style="color:green;">&#9679;</span> Start<br>
        <span style="color:red;">&#9679;</span> Finish<br>
        <span style="color:orange;">&#9679;</span> Fuel Stop<br>
        <span style="color:#1A73E8;">&#9473;</span> Route
    </div>
    """
    fmap.get_root().html.add_child(folium.Element(legend_html))

    # Return the complete standalone HTML string
    return fmap.get_root().render()
