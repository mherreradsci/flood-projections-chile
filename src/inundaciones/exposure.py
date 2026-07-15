"""Exposición: infraestructura dentro de la extensión proyectada.

Estrategia robusta ante límites de Overpass:
 1. Vías y servicios críticos OSM se descargan UNA vez para toda la región
    (bbox simple) y se cachean en data/vector/. Las corridas posteriores no
    tocan la red.
 2. La intersección con cada escenario es local (geopandas).
 3. La superficie urbana afectada se estima con WorldCover clase 50 (local),
    en vez de contar edificios OSM (consulta demasiado pesada).
"""

import json
from pathlib import Path

import geopandas as gpd
import numpy as np

from .utils import (area_celda_m2, cargar_config, leer_raster, log, ruta_data,
                    ruta_outputs)

TIMEOUT_S = 600


def _configurar_osmnx(cfg: dict):
    import osmnx as ox
    ox.settings.overpass_url = cfg.get("exposicion", {}).get(
        "overpass_endpoint", "https://overpass.kumi.systems/api")
    ox.settings.overpass_rate_limit = False
    ox.settings.requests_timeout = TIMEOUT_S
    return ox


def descargar_osm_region(cfg: dict) -> dict[str, Path]:
    """Vías y servicios de toda la región, cacheados en data/vector/."""
    ruta_vias = ruta_data(cfg, "vector", "osm_vias.gpkg")
    ruta_serv = ruta_data(cfg, "vector", "osm_servicios.gpkg")
    if ruta_vias.exists() and ruta_serv.exists():
        return {"vias": ruta_vias, "servicios": ruta_serv}

    ox = _configurar_osmnx(cfg)
    o, s, e, n = cfg["region"]["bbox"]

    if not ruta_serv.exists():
        serv = ox.features_from_bbox((o, s, e, n),
                                     {"amenity": cfg["exposicion"]["servicios"]})
        serv = serv.reset_index()[["amenity", "name", "geometry"]]
        serv["geometry"] = serv.geometry.centroid
        serv.to_file(ruta_serv, driver="GPKG")
        log.info("OSM servicios región: %d puntos", len(serv))

    if not ruta_vias.exists():
        vias = ox.features_from_bbox((o, s, e, n),
                                     {"highway": cfg["exposicion"]["vias"]})
        vias = vias.reset_index()
        vias = vias[vias.geometry.geom_type.isin(["LineString", "MultiLineString"])]
        vias[["highway", "geometry"]].to_file(ruta_vias, driver="GPKG")
        log.info("OSM vías región: %d tramos", len(vias))

    return {"vias": ruta_vias, "servicios": ruta_serv}


def evaluar_exposicion(cfg: dict, sufijo: str = "proyectada") -> Path:
    zonas = gpd.read_file(ruta_outputs(cfg, f"zonas_nuevas_{sufijo}.geojson"))
    recurrentes = gpd.read_file(ruta_outputs(cfg, f"zonas_recurrentes_{sufijo}.geojson"))
    todas = gpd.GeoDataFrame(geometry=list(zonas.geometry) + list(recurrentes.geometry),
                             crs="EPSG:4326")
    resumen = {"vias_km": 0.0, "urbano_ha": 0.0, "servicios": []}
    destino = ruta_outputs(cfg, f"exposicion_{sufijo}.json")
    if todas.empty:
        destino.write_text(json.dumps(resumen, indent=2))
        return destino

    poligono = todas.union_all().buffer(0.0005)  # ~50 m de tolerancia

    # superficie urbana anegada (WorldCover 50) — cálculo local
    ext, transform, _ = leer_raster(ruta_outputs(cfg, f"extension_{sufijo}.tif"))
    lc = leer_raster(ruta_data(cfg, "landcover", "worldcover.tif"))[0]
    lat_media = (cfg["region"]["bbox"][1] + cfg["region"]["bbox"][3]) / 2
    celda_ha = area_celda_m2(transform, lat_media) / 1e4
    resumen["urbano_ha"] = round(float(((ext == 1) & (lc == 50)).sum() * celda_ha), 1)

    try:
        capas = descargar_osm_region(cfg)
        serv = gpd.read_file(capas["servicios"])
        dentro = serv[serv.geometry.within(poligono)]
        resumen["servicios"] = [
            {"tipo": f.amenity, "nombre": f.get("name") or "s/n",
             "lon": f.geometry.x, "lat": f.geometry.y}
            for f in dentro.itertuples()]

        vias = gpd.read_file(capas["vias"])
        afectadas = gpd.clip(vias, poligono)
        if not afectadas.empty:
            resumen["vias_km"] = round(float(
                afectadas.to_crs(32719).geometry.length.sum() / 1000), 1)
            afectadas[["geometry"]].to_file(
                ruta_outputs(cfg, f"vias_expuestas_{sufijo}.geojson"),
                driver="GeoJSON")
    except Exception as exc:
        log.warning("Capas OSM no disponibles (%s); exposición solo con "
                    "superficie urbana", exc)

    destino.write_text(json.dumps(resumen, indent=2, ensure_ascii=False))
    log.info("Exposición '%s': %.1f km de vías, %.0f ha urbanas, %d servicios",
             sufijo, resumen["vias_km"], resumen["urbano_ha"],
             len(resumen["servicios"]))
    return destino


if __name__ == "__main__":
    cfg = cargar_config()
    print(evaluar_exposicion(cfg))
