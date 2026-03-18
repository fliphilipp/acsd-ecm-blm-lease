# +
# %load_ext autoreload
# %autoreload 2
# %matplotlib widget

import geopandas as gpd
import leafmap.foliumap as leafmap
import folium

# +
# Paths
lease_boundary_fp = "output/lease_boundary.shp"
lease_points_fp = "output/lease_points.shp"

# Read shapefiles
lease_boundary = gpd.read_file(lease_boundary_fp)
lease_points = gpd.read_file(lease_points_fp)
# -

lease_boundary

lease_points

# +
import math
import html

import geopandas as gpd
import folium


lease_boundary = gpd.read_file("output/lease_boundary.shp").to_crs(4326)
lease_points = gpd.read_file("output/lease_points.shp").to_crs(4326)

# --- map center ---
center = [
    lease_boundary.geometry.centroid.y.iloc[0],
    lease_boundary.geometry.centroid.x.iloc[0],
]

# --- create map with no default base tiles so we can control them explicitly ---
m = folium.Map(location=center, zoom_start=16, tiles=None, control_scale=True, height="800px", width="1000px")

# --- selectable basemaps ---
folium.TileLayer(
    tiles="OpenStreetMap",
    name="OpenStreetMap",
    control=True,
).add_to(m)

folium.TileLayer(
    tiles="CartoDB Positron",
    name="CartoDB Positron",
    control=True,
).add_to(m)

folium.TileLayer(
    tiles="https://server.arcgisonline.com/ArcGIS/rest/services/"
          "World_Imagery/MapServer/tile/{z}/{y}/{x}",
    attr="Esri",
    name="Satellite (Esri)",
    overlay=False,
    control=True,
).add_to(m)

# --- feature groups for selectable overlays ---
fg_boundary_polygon = folium.FeatureGroup(name="Lease boundary polygon", show=True)
fg_boundary_points = folium.FeatureGroup(name="Boundary points", show=True)
fg_signage = folium.FeatureGroup(name="Signage", show=True)

# --- boundary polygon ---
folium.GeoJson(
    lease_boundary,
    name="Lease boundary",
    style_function=lambda x: {
        "color": "red",
        "weight": 3,
        "fillColor": "red",
        "fillOpacity": 0.15,
    },
    tooltip=folium.GeoJsonTooltip(
        fields=[c for c in lease_boundary.columns if c != "geometry"],
        aliases=[c for c in lease_boundary.columns if c != "geometry"],
        sticky=False,
    ),
).add_to(fg_boundary_polygon)


# -------------------------------------------------------------------
# helper: offset boundary labels so they sit outside the polygon
# -------------------------------------------------------------------
poly_centroid = lease_boundary.geometry.iloc[0].centroid
cx, cy = poly_centroid.x, poly_centroid.y

def outward_label_position(x, y, cx, cy, offset_deg=0.00022):
    """
    Move label away from polygon centroid so it appears outside polygon.
    offset_deg is in degrees because map is EPSG:4326.
    """
    dx = x - cx
    dy = y - cy
    norm = math.hypot(dx, dy)

    # fallback in pathological case
    if norm == 0:
        return x, y + offset_deg

    ux = dx / norm
    uy = dy / norm
    return x + ux * offset_deg, y + uy * offset_deg


# -------------------------------------------------------------------
# helper: signage icon choice
# -------------------------------------------------------------------
def get_sign_style(sign_typ):
    sign_typ = (sign_typ or "").lower()

    if "informational" in sign_typ:
        return {
            "icon": folium.Icon(color="blue", icon="info-sign", prefix="glyphicon"),
            "color": "blue",
        }
    elif "regulatory" in sign_typ:
        return {
            "icon": folium.Icon(color="black", icon="ban", prefix="fa"),
            "color": "black",
        }
    elif "boundary" in sign_typ:
        return {
            "icon": folium.Icon(color="green", icon="flag", prefix="fa"),
            "color": "green",
        }
    else:
        return {
            "icon": folium.Icon(color="purple", icon="arrow-right", prefix="fa"),
            "color": "purple",
        }


# -------------------------------------------------------------------
# boundary points
# -------------------------------------------------------------------
boundary_pts = lease_points[lease_points.feat_kind == "boundary"].copy()

def make_boundary_label_icon(label, side, width_px=120):
    """
    side: 'left' or 'right' relative to polygon centroid
    """
    if side == "left":
        # place label box to the LEFT of anchor point
        translate = f"translateX(-{width_px}px)"
        align = "right"
    else:
        # place label box to the RIGHT of anchor point
        translate = "translateX(0)"
        align = "left"

    return folium.DivIcon(
        icon_size=(width_px, 20),
        icon_anchor=(0, 10),
        html=f"""
        <div style="
            width: {width_px}px;
            font-size: 11px;
            color: red;
            white-space: nowrap;
            text-align: {align};
            font-weight: 600;
            transform: {translate};
            pointer-events: none;
            text-shadow:
                -1px -1px 0 white,
                 1px -1px 0 white,
                -1px  1px 0 white,
                 1px  1px 0 white;
        ">
            {html.escape(label)}
        </div>
        """
    )

for _, row in boundary_pts.iterrows():
    y = row.geometry.y
    x = row.geometry.x
    label = str(row.label)

    # point marker
    folium.CircleMarker(
        location=[y, x],
        radius=5,
        color="darkred",
        weight=2,
        fill=True,
        fill_color="white",
        fill_opacity=1.0,
        popup=folium.Popup(html.escape(label), max_width=250),
        tooltip=label,
    ).add_to(fg_boundary_points)

    # offset label outward from polygon
    lx, ly = outward_label_position(x, y, cx, cy, offset_deg=0.0004)

    # pick left/right alignment depending on side of centroid
    side = "right" if x >= cx else "left"

    folium.Marker(
        [ly, lx],
        icon=make_boundary_label_icon(label, side, width_px=120),
    ).add_to(fg_boundary_points)


# -------------------------------------------------------------------
# signage points
# -------------------------------------------------------------------
signage_pts = lease_points[lease_points.feat_kind == "signage"].copy()

def make_centered_sign_label_icon(label, text_color, width_px=140):
    return folium.DivIcon(
        icon_size=(width_px, 20),
        icon_anchor=(width_px // 2, 0),
        html=f"""
        <div style="
            width: {width_px}px;
            font-size: 11px;
            color: {text_color};
            white-space: nowrap;
            text-align: center;
            pointer-events: none;
            text-shadow:
                -1px -1px 0 white,
                 1px -1px 0 white,
                -1px  1px 0 white,
                 1px  1px 0 white;
        ">
            {html.escape(label)}
        </div>
        """
    )

for _, row in signage_pts.iterrows():
    y = row.geometry.y
    x = row.geometry.x
    label = str(row.label)
    sign_typ = row.get("sign_typ", "")
    status = row.get("status", "")

    style = get_sign_style(sign_typ)
    icon = style["icon"]
    text_color = style["color"]

    popup_html = f"""
    <b>{html.escape(label)}</b><br>
    type: {html.escape(str(sign_typ))}<br>
    status: {html.escape(str(status))}
    """

    folium.Marker(
        location=[y, x],
        icon=icon,
        popup=folium.Popup(popup_html, max_width=300),
        tooltip=label,
    ).add_to(fg_signage)

    folium.Marker(
        [y - 0.00001, x],
        icon=make_centered_sign_label_icon(label, text_color, width_px=150),
    ).add_to(fg_signage)


# --- add overlay layers ---
fg_boundary_polygon.add_to(m)
fg_boundary_points.add_to(m)
fg_signage.add_to(m)

# optional extras
folium.LayerControl(collapsed=False).add_to(m)

m.save("output/lease_map_from_shapefiles.html")

m
# -


