"""Configuration for the Surf Spot Climate Resilience Explorer pre-processing pipeline.

All data sources are UK public open data published under the Open Government
Licence v3.0. No API keys or secrets are required.

V1 scope (this skeleton): sea level rise + coastal erosion risk only.
Sea surface temperature (PRD section 4.4) is intentionally deferred.
"""

# --- Sea level rise: Met Office UKCP18, ArcGIS FeatureServer -----------------
# Polygon layer; each polygon covers ~12 km of coastline. Fields are decadal
# mean sea level anomalies (cm) relative to the 1981-2000 baseline.
SEA_LEVEL_URL = (
    "https://services.arcgis.com/Lq3V5RFuTBC9I7kv/arcgis/rest/services/"
    "Sea_Level_2010_2100/FeatureServer/1"
)
# RCP8.5, 50th percentile is the agreed default (PRD open question #1, decided).
# Note: the service stores this value lower-cased as "rcp85_50".
SEA_LEVEL_RCP_PERCENTILE = "rcp85_50"
SEA_LEVEL_DECADES = [2030, 2040, 2050, 2060, 2070, 2080, 2090, 2100]
# Breaks sit just inshore of the offshore sea-level polygons, so a plain
# point-in-polygon test returns nothing. We buffer the point outward until we
# hit polygons, trying these radii (metres) in order.
SEA_LEVEL_BUFFERS_M = [2000, 6000, 12000, 25000]

# --- Coastal erosion risk: NCERM 2024, Environment Agency OGC API ------------
# NB: the official endpoint contains a spelling quirk ("ncern", not "ncerm").
EROSION_BASE_URL = (
    "https://environment.data.gov.uk/spatialdata/ncern-national-2024/"
    "ogc/features/v1"
)
# Climate allowance to use for erosion zones. NCERM offers three:
#   0CC  = Present Day climate (2020)
#   70CC = Higher Central allowance (UKCP18 RCP8.5 70th percentile)
#   95CC = Upper End allowance    (UKCP18 RCP8.5 95th percentile)
# We default to the Higher Central allowance as a central planning estimate,
# consistent with the RCP8.5 default used for sea level.
EROSION_CC = "70"
# Collections we query, mapped to the PRD output fields. SMP collections carry
# the human-readable policy text (mt_smp / lt_smp + interpretation); NFI
# collections carry only a numeric recession band.
EROSION_COLLECTIONS = {
    "medium_term_with_smp": f"NCERM_SMP_2055_{EROSION_CC}CC",
    "long_term_with_smp": f"NCERM_SMP_2105_{EROSION_CC}CC",
    "medium_term_no_intervention": f"NCERM_NFI_2055_{EROSION_CC}CC",
    "long_term_no_intervention": f"NCERM_NFI_2105_{EROSION_CC}CC",
}
# bbox half-widths (degrees) tried in order when locating the nearest frontage.
# ~0.02 deg latitude is roughly 2.2 km.
EROSION_BBOX_DELTAS = [0.02, 0.05, 0.1, 0.2]
# If the nearest mapped frontage is further than this, we treat the break as
# having no nearby erosion mapping rather than inventing a classification
# (PRD open question #2: not-at-risk vs data gap).
EROSION_MAX_DISTANCE_M = 2000

# --- HTTP behaviour ----------------------------------------------------------
HTTP_TIMEOUT_S = 90
HTTP_RETRIES = 4
HTTP_BACKOFF_S = 3
# Be polite to the (sometimes slow) Environment Agency server.
INTER_REQUEST_DELAY_S = 0.3

# --- Paths -------------------------------------------------------------------
INPUT_CSV = "surf_breaks.csv"
OUTPUT_JSON = "data/surf_breaks.json"
