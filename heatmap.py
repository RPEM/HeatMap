import pandas as pd
import folium
from folium.plugins import HeatMap, MarkerCluster
import json

import geopandas as gpd
from folium import MacroElement
from jinja2 import Template
from folium.features import DivIcon

# =========================
# SETTINGS / MAPPINGS
# =========================

CODE_TO_NAME = {
    "AB": "Alberta",
    "BC": "British Columbia",
    "MB": "Manitoba",
    "NB": "New Brunswick",
    "NL": "Newfoundland and Labrador",
    "NS": "Nova Scotia",
    "NT": "Northwest Territories",
    "NU": "Nunavut",
    "ON": "Ontario",
    "PE": "Prince Edward Island",
    "QC": "Quebec",
    "SK": "Saskatchewan",
    "YK": "Yukon",
}

# Regions by FULL province names (must match GeoJSON properties["name"])
PURPLE = {"Nunavut", "Northwest Territories", "Yukon", "British Columbia"}
GREEN  = {"Ontario", "Manitoba", "Saskatchewan", "Alberta", "Quebec"}
ORANGE = {"Newfoundland and Labrador", "Prince Edward Island", "Nova Scotia", "New Brunswick"}

REGION_COLOR = {
    "Purple Region": "purple",
    "Green Region": "green",
    "Orange Region": "orange",
}

def province_to_region(prov_name: str):
    if prov_name in PURPLE:
        return "Purple Region"
    if prov_name in GREEN:
        return "Green Region"
    if prov_name in ORANGE:
        return "Orange Region"
    return None

def add_count_marker(feature_group, lat, lon, count, color, tooltip):
    # pointer-events:none makes it click-through
    html = f"""
    <div style="
        pointer-events:none;
        background:{color};
        border:2px solid black;
        border-radius:50%;
        width:46px; height:46px;
        display:flex;
        align-items:center;
        justify-content:center;
        font-weight:bold;
        color:white;
        font-size:14px;
        ">
        {count}
    </div>
    """
    folium.Marker(
        location=[lat, lon],
        icon=DivIcon(html=html),
        tooltip=tooltip,
        interactive=False  # also makes marker not capture clicks
    ).add_to(feature_group)

# =========================
# LOAD + CLEAN DATA
# =========================

df = pd.read_excel(
    "10-20-2025_Site List.xlsx",
    sheet_name="10-20_Site List Raw",
    engine="openpyxl"
)

df.columns = df.columns.str.replace("\n", " ").str.strip()

valid_users = ["DFO", "Shared-DFO", "SCH"]
df = df[df["Site User 10-20-2025"].isin(valid_users)]

df = df.dropna(subset=["Latitude", "Longitude"])
df = df[(df["Latitude"].between(41.7, 83.1)) & (df["Longitude"].between(-141.0, -52.6))]

df["Category"] = pd.to_numeric(df["Category"], errors="coerce").fillna(0)

# Province code column (adjust if your column name differs)
df["ProvCode"] = df["Province"].astype(str).str.strip().str.upper()

# Treat OC as BC (temporary rule)
df["ProvCode"] = df["ProvCode"].replace({"OC": "BC"})

# Convert to full names
df["province_name"] = df["ProvCode"].map(CODE_TO_NAME)

# Assign region
df["region"] = df["province_name"].apply(province_to_region)

# Keep only rows with valid province + region
df = df[df["province_name"].notna() & df["region"].notna()].copy()

# =========================
# CREATE MAP
# =========================

center_lat = df["Latitude"].mean()
center_lon = df["Longitude"].mean()
START_ZOOM = 4

m = folium.Map(location=[center_lat, center_lon], zoom_start=START_ZOOM, tiles="OpenStreetMap")

# =========================
# LOAD GEOJSON
# =========================

with open("ca.json", "r", encoding="utf-8") as f:
    canada_gj = json.load(f)

prov_gdf = gpd.GeoDataFrame.from_features(canada_gj["features"])
# Assumes GeoJSON has properties key "name"
prov_gdf["name"] = prov_gdf["name"].astype(str).str.strip()

prov_gdf["region"] = prov_gdf["name"].apply(province_to_region)
prov_gdf = prov_gdf[prov_gdf["region"].notna()].copy()

regions_gdf = prov_gdf.dissolve(by="region", as_index=False)

regions_geojson = json.loads(regions_gdf.to_json())
provinces_geojson = json.loads(prov_gdf.to_json())

# =========================
# COUNTS
# =========================

counts_region = df.groupby("region").size().to_dict()
counts_prov = df.groupby(["region", "province_name"]).size().to_dict()

# =========================
# COUNT MARKER LAYERS
# =========================

region_counts_layer = folium.FeatureGroup(name="Region counts", show=True).add_to(m)

prov_counts_purple = folium.FeatureGroup(name="Province counts (Purple)", show=False).add_to(m)
prov_counts_green  = folium.FeatureGroup(name="Province counts (Green)", show=False).add_to(m)
prov_counts_orange = folium.FeatureGroup(name="Province counts (Orange)", show=False).add_to(m)

prov_counts_layer_by_region = {
    "Purple Region": prov_counts_purple,
    "Green Region": prov_counts_green,
    "Orange Region": prov_counts_orange,
}

# Region count markers at region centroids
regions_tmp = regions_gdf.copy()
regions_tmp["centroid"] = regions_tmp.geometry.centroid

for _, r in regions_tmp.iterrows():
    reg = r["region"]
    c = r["centroid"]
    count = counts_region.get(reg, 0)
    add_count_marker(
        region_counts_layer,
        c.y, c.x,
        count=count,
        color=REGION_COLOR[reg],
        tooltip=f"{reg}: {count} sites"
    )

# Province count markers at province centroids (in separate layers)
prov_tmp = prov_gdf.copy()
prov_tmp["centroid"] = prov_tmp.geometry.centroid

for _, pr in prov_tmp.iterrows():
    reg = pr["region"]
    prov_name = pr["name"]
    c = pr["centroid"]
    count = counts_prov.get((reg, prov_name), 0)
    add_count_marker(
        prov_counts_layer_by_region[reg],
        c.y, c.x,
        count=count,
        color=REGION_COLOR[reg],
        tooltip=f"{prov_name}: {count} sites"
    )

# =========================
# PROVINCE POLYGONS (hidden until region click)
# =========================

def province_poly_style(feature):
    reg = feature["properties"]["region"]
    color = REGION_COLOR.get(reg, "gray")
    return {
        "fillColor": color,
        "color": "black",
        "weight": 1,
        "fillOpacity": 0.0,  # hidden at start
    }

province_poly_layer = folium.GeoJson(
    provinces_geojson,
    name="Provinces (click after region)",
    style_function=province_poly_style,
    tooltip=folium.GeoJsonTooltip(fields=["name", "region"])
).add_to(m)

# =========================
# REGION POLYGONS (clickable)
# =========================

def region_style(feature):
    reg = feature["properties"]["region"]
    return {
        "fillColor": REGION_COLOR[reg],
        "color": "black",
        "weight": 2,
        "fillOpacity": 0.25
    }

region_layer = folium.GeoJson(
    regions_geojson,
    name="Regions (click to zoom)",
    style_function=region_style,
    tooltip=folium.GeoJsonTooltip(fields=["region"])
).add_to(m)

# =========================
# PROVINCE-SPECIFIC HEATMAP + MARKERS (hidden until province click)
# =========================

province_site_layers = {}  # province_name -> FeatureGroup

for prov_name, sub in df.groupby("province_name"):
    fg = folium.FeatureGroup(name=f"{prov_name} (sites + heat)", show=False).add_to(m)
    province_site_layers[prov_name] = fg

    heat_data = [
        [row["Latitude"], row["Longitude"], 1 if row["Category"] == 1 else 0.2]
        for _, row in sub.iterrows()
    ]

    HeatMap(
        heat_data,
        radius=11,
        blur=10,
        max_zoom=6,
        min_opacity=0.4,
        gradient={0.2: "green", 0.4: "yellow", 0.6: "orange", 1.0: "red"}
    ).add_to(fg)

    cluster = MarkerCluster().add_to(fg)
    for _, row in sub.iterrows():
        popup_text = (
            f"Site: {row.get('Site Name', 'N/A')}<br>"
            f"User: {row['Site User 10-20-2025']}<br>"
            f"Category: {row['Category']}<br>"
            f"Province: {prov_name}"
        )
        folium.Marker(
            location=[row["Latitude"], row["Longitude"]],
            popup=popup_text
        ).add_to(cluster)

prov_site_map = {prov: fg.get_name() for prov, fg in province_site_layers.items()}
prov_site_map_json = json.dumps(prov_site_map)

# =========================
# JS: Back button + Drill-down logic
# =========================

map_var = m.get_name()
region_var = region_layer.get_name()
provpoly_var = province_poly_layer.get_name()

region_counts_var = region_counts_layer.get_name()
prov_counts_purple_var = prov_counts_purple.get_name()
prov_counts_green_var  = prov_counts_green.get_name()
prov_counts_orange_var = prov_counts_orange.get_name()

click_js = f"""
{{% macro script(this, kwargs) %}}
var provSiteMap = {prov_site_map_json};
var currentRegion = null;

// Layer helpers
function removeLayerIfPresent(layerObj) {{
  if (layerObj && {map_var}.hasLayer(layerObj)) {{
    {map_var}.removeLayer(layerObj);
  }}
}}

function addLayerIfMissing(layerObj) {{
  if (layerObj && !{map_var}.hasLayer(layerObj)) {{
    {map_var}.addLayer(layerObj);
  }}
}}

function hideAllProvinceSiteLayers() {{
  Object.keys(provSiteMap).forEach(function(provName) {{
    var layerName = provSiteMap[provName];
    var layerObj = window[layerName];
    removeLayerIfPresent(layerObj);
  }});
}}

function showProvinceSiteLayer(provName) {{
  hideAllProvinceSiteLayers();
  var layerName = provSiteMap[provName];
  var layerObj = window[layerName];
  addLayerIfMissing(layerObj);
}}

// Province polygons visibility
function setProvincePolygonVisibility(targetRegion) {{
  {provpoly_var}.eachLayer(function(layer) {{
    var props = layer.feature && layer.feature.properties ? layer.feature.properties : {{}};
    var isIn = (props.region === targetRegion);
    layer.setStyle({{
      fillOpacity: isIn ? 0.35 : 0.0,
      weight: isIn ? 1 : 0
    }});
  }});
}}

function hideAllProvinceCounts() {{
  removeLayerIfPresent(window["{prov_counts_purple_var}"]);
  removeLayerIfPresent(window["{prov_counts_green_var}"]);
  removeLayerIfPresent(window["{prov_counts_orange_var}"]);
}}

function showProvinceCountsForRegion(targetRegion) {{
  hideAllProvinceCounts();
  if (targetRegion === "Purple Region") addLayerIfMissing(window["{prov_counts_purple_var}"]);
  if (targetRegion === "Green Region")  addLayerIfMissing(window["{prov_counts_green_var}"]);
  if (targetRegion === "Orange Region") addLayerIfMissing(window["{prov_counts_orange_var}"]);
}}

// BACK button control
var BackControl = L.Control.extend({{
  options: {{ position: 'topright' }},
  onAdd: function(map) {{
    var container = L.DomUtil.create('div', 'leaflet-bar leaflet-control');
    container.style.background = 'white';
    container.style.padding = '6px 8px';
    container.style.cursor = 'pointer';
    container.style.fontSize = '14px';
    container.style.userSelect = 'none';
    container.innerHTML = '⬅ Back to Regions';

    // prevent map dragging/zooming when clicking the button
    L.DomEvent.disableClickPropagation(container);

    container.onclick = function() {{
      // Reset state
      currentRegion = null;

      // Show region counts
      addLayerIfMissing(window["{region_counts_var}"]);

      // Hide province counts
      hideAllProvinceCounts();

      // Hide provinces polygons
      setProvincePolygonVisibility("__NONE__");

      // Hide province site layers
      hideAllProvinceSiteLayers();

      // Reset view
      map.setView([{center_lat}, {center_lon}], {START_ZOOM});
    }};

    return container;
  }}
}});
{map_var}.addControl(new BackControl());

// REGION click
{region_var}.eachLayer(function(layer) {{
  layer.on('click', function() {{
    var targetRegion = layer.feature.properties.region;
    {provpoly_var}.bringToFront();

    {map_var}.fitBounds(layer.getBounds());

    // hide region counts once drilled in
    removeLayerIfPresent(window["{region_counts_var}"]);

    // show province counts for that region
    showProvinceCountsForRegion(targetRegion);

    // reveal province polygons for that region
    setProvincePolygonVisibility(targetRegion);

    // hide any previously shown province sites
    hideAllProvinceSiteLayers();
  }});
}});

// PROVINCE polygon click
// PROVINCE polygon click (always allowed)
{provpoly_var}.eachLayer(function(layer) {{
  layer.on('click', function() {{
    var props = layer.feature.properties;
    {provpoly_var}.bringToFront();

    // Auto-select the province's region
    currentRegion = props.region;

    // Hide region counts and show province counts
    removeLayerIfPresent(window["{region_counts_var}"]);
    showProvinceCountsForRegion(currentRegion);

    // Reveal provinces in this region
    setProvincePolygonVisibility(currentRegion);

    // Zoom and show this province's sites
    {map_var}.fitBounds(layer.getBounds());
    showProvinceSiteLayer(props.name);
  }});
}});
{{% endmacro %}}
"""

macro = MacroElement()
macro._template = Template(click_js)
m.get_root().add_child(macro)

# =========================
# LEGEND
# =========================

legend_html = """
<div style="position: fixed;
     bottom: 50px; left: 50px; width: 240px;
     background-color: white; border:2px solid grey;
     z-index:9999; font-size:14px; padding:10px;">
     <b>Heatmap Legend</b><br>
     <span style="color:red;">●</span> High concentration (Category 1)<br>
     <span style="color:orange;">●</span> Moderate<br>
     <span style="color:yellow;">●</span> Low<br>
     <span style="color:green;">●</span> Few or none<br>
</div>
"""
m.get_root().html.add_child(folium.Element(legend_html))

# =========================
# SAVE
# =========================

folium.LayerControl().add_to(m)
m.save("site_heatmap.html")
print("Saved: site_heatmap.html")
