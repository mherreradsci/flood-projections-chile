"""Área de interés: límite de la Región de Coquimbo y subcuencas HydroBASINS."""

from pathlib import Path

import geopandas as gpd
import requests
from shapely.geometry import box

from .utils import cargar_config, log, ruta_data

URL_HYDROBASINS = (
    "https://data.hydrosheds.org/file/HydroBASINS/standard/hybas_sa_lev{nivel:02d}_v1c.zip"
)


def obtener_region(cfg: dict) -> gpd.GeoDataFrame:
    """Límite regional desde OSM (Nominatim); respaldo: bbox de config."""
    destino = ruta_data(cfg, "vector", "region.geojson")
    if destino.exists():
        return gpd.read_file(destino)
    try:
        import osmnx as ox
        gdf = ox.geocode_to_gdf(cfg["region"]["osm_geocode"])
        gdf = gdf[["geometry"]]
        log.info("Límite regional obtenido de OSM")
    except Exception as e:  # sin red o Nominatim caído
        log.warning("OSM no disponible (%s); uso bbox de config", e)
        gdf = gpd.GeoDataFrame(geometry=[box(*cfg["region"]["bbox"])], crs="EPSG:4326")
    gdf.to_file(destino, driver="GeoJSON")
    return gdf


def obtener_subcuencas(cfg: dict) -> gpd.GeoDataFrame:
    """Subcuencas HydroBASINS recortadas a la región."""
    destino = ruta_data(cfg, "vector", "subcuencas.geojson")
    if destino.exists():
        return gpd.read_file(destino)

    nivel = cfg["subcuencas"]["hydrobasins_nivel"]
    zip_local = ruta_data(cfg, "vector", f"hybas_sa_lev{nivel:02d}_v1c.zip")
    if not zip_local.exists():
        url = URL_HYDROBASINS.format(nivel=nivel)
        log.info("Descargando HydroBASINS nivel %d: %s", nivel, url)
        r = requests.get(url, timeout=600)
        r.raise_for_status()
        zip_local.write_bytes(r.content)

    region = obtener_region(cfg)
    bbox = tuple(region.total_bounds)
    cuencas = gpd.read_file(f"zip://{zip_local}", bbox=bbox)
    # conservar subcuencas cuyo punto representativo cae en la región
    # (representative_point no dispara el warning de CRS geográfico y
    # siempre queda dentro del polígono, a diferencia del centroide)
    dentro = cuencas[cuencas.geometry.representative_point().within(region.union_all())]
    if dentro.empty:
        dentro = cuencas
    dentro = dentro[["HYBAS_ID", "NEXT_DOWN", "SUB_AREA", "geometry"]].copy()
    dentro["HYBAS_ID"] = dentro["HYBAS_ID"].astype("int64")
    dentro.to_file(destino, driver="GeoJSON")
    log.info("Subcuencas en la región: %d", len(dentro))
    return dentro


if __name__ == "__main__":
    cfg = cargar_config()
    region = obtener_region(cfg)
    print("Región:", region.total_bounds)
    sub = obtener_subcuencas(cfg)
    print("Subcuencas:", len(sub))
