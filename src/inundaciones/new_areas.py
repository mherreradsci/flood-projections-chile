"""Zonas nuevas: extensión proyectada fuera de las huellas históricas observadas."""

from pathlib import Path

import geopandas as gpd
import numpy as np
from rasterio import features
from shapely.geometry import shape

from .utils import (
    area_celda_m2,
    cargar_config,
    guardar_raster,
    leer_raster,
    log,
    ruta_data,
    ruta_outputs,
)

AREA_MINIMA_CELDAS = 4  # descarta manchas menores (ruido de remuestreo)


def _vectorizar(mascara: np.ndarray, transform) -> gpd.GeoDataFrame:
    geoms = [shape(g) for g, v in features.shapes(
        mascara.astype("uint8"), mask=mascara, transform=transform) if v == 1]
    return gpd.GeoDataFrame(geometry=geoms, crs="EPSG:4326")


def _filtrar_por_area_minima(gdf: gpd.GeoDataFrame, celda_km2: float) -> gpd.GeoDataFrame:
    """Descarta polígonos menores a AREA_MINIMA_CELDAS celdas (ruido de remuestreo).

    El área se mide reproyectando a UTM 19S (EPSG:32719): medirla en el CRS
    geográfico original (grados) sesga con cos(lat) y no representa área real.
    """
    if gdf.empty:
        return gdf
    gdf["area_km2"] = gdf.geometry.to_crs(32719).area / 1e6
    return gdf[gdf.area_km2 >= AREA_MINIMA_CELDAS * celda_km2]


def identificar_zonas_nuevas(cfg: dict, sufijo: str = "proyectada") -> dict[str, Path]:
    extension, transform, _ = leer_raster(ruta_outputs(cfg, f"extension_{sufijo}.tif"))
    ruta_union = ruta_data(cfg, "historical", "huella_historica_union.tif")
    historico = (leer_raster(ruta_union)[0] == 1) if ruta_union.exists() \
        else np.zeros(extension.shape, dtype=bool)

    proyectado = extension == 1
    nuevas = proyectado & ~historico
    recurrentes = proyectado & historico

    lat_media = (cfg["region"]["bbox"][1] + cfg["region"]["bbox"][3]) / 2
    celda_km2 = area_celda_m2(transform, lat_media) / 1e6

    rutas = {}
    for nombre, mascara in [("zonas_nuevas", nuevas), ("zonas_recurrentes", recurrentes)]:
        raster = ruta_outputs(cfg, f"{nombre}_{sufijo}.tif")
        guardar_raster(raster, mascara.astype("uint8"), transform, nodata=255,
                       dtype="uint8")
        gdf = _vectorizar(mascara, transform)
        gdf = _filtrar_por_area_minima(gdf, celda_km2)
        geojson = ruta_outputs(cfg, f"{nombre}_{sufijo}.geojson")
        gdf.to_file(geojson, driver="GeoJSON")
        rutas[nombre] = geojson
        log.info("%s: %.1f km² en %d polígonos", nombre,
                 float(mascara.sum()) * celda_km2, len(gdf))
    return rutas


if __name__ == "__main__":
    cfg = cargar_config()
    print(identificar_zonas_nuevas(cfg))
