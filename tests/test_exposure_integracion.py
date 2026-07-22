"""Test de integración de evaluar_exposicion: vectores/rasters sintéticos en
tmp_path, sin red. Ver tests/test_flood_model_integracion.py para el porqué
de esta categoría (documentada en CLAUDE.md).

descargar_osm_region() cachea por existencia de archivo (osm_vias.gpkg y
osm_servicios.gpkg): precrearlos evita por completo Overpass/Geofabrik.
"""

import json

import geopandas as gpd
import numpy as np
from rasterio.transform import Affine
from shapely.geometry import LineString, Point, box

from inundaciones.exposure import _area_urbana_ha, evaluar_exposicion
from inundaciones.utils import area_celda_m2, guardar_raster, ruta_data, ruta_outputs

TRANSFORM = Affine(0.001, 0.0, -71.0, 0.0, -0.001, -30.0)
LAT_MEDIA = -30.0


def _cfg(tmp_path):
    return {
        "rutas": {"data": str(tmp_path / "data"), "outputs": str(tmp_path / "outputs")},
        "region": {"bbox": [-71.01, -30.02, -70.99, -29.98]},  # lat_media = -30.0
    }


def test_evaluar_exposicion_combina_urbano_raster_y_capas_osm(tmp_path):
    cfg = _cfg(tmp_path)
    sufijo = "test"

    # dos zonas anegadas contiguas -> se unen en un solo polígono de exposición
    zonas_nuevas = gpd.GeoDataFrame(
        geometry=[box(-70.997, -30.006, -70.993, -30.002)], crs="EPSG:4326")
    zonas_recurrentes = gpd.GeoDataFrame(
        geometry=[box(-70.993, -30.006, -70.991, -30.002)], crs="EPSG:4326")
    zonas_nuevas.to_file(ruta_outputs(cfg, f"zonas_nuevas_{sufijo}.geojson"), driver="GeoJSON")
    zonas_recurrentes.to_file(
        ruta_outputs(cfg, f"zonas_recurrentes_{sufijo}.geojson"), driver="GeoJSON")

    # superficie urbana anegada: 3x3 anegado y 3x3 urbano se solapan en 2x2 = 4 celdas
    extension = np.zeros((10, 10), dtype="uint8")
    extension[2:5, 2:5] = 1
    worldcover = np.full((10, 10), 10, dtype="uint8")  # 10 = no urbano
    worldcover[3:6, 3:6] = 50  # 50 = urbano (ESA WorldCover)
    guardar_raster(tmp_path / "outputs" / f"extension_{sufijo}.tif", extension, TRANSFORM,
                   nodata=255, dtype="uint8")
    guardar_raster(tmp_path / "data" / "landcover" / "worldcover.tif", worldcover, TRANSFORM,
                   nodata=255, dtype="uint8")

    # capas OSM cacheadas: dos servicios dentro del polígono (uno sin nombre) y
    # uno lejos; una vía que cruza el polígono y otra que no lo toca
    servicios = gpd.GeoDataFrame(
        {"amenity": ["hospital", "school", "hospital"],
         "name": ["Hospital Test", None, "Lejano"]},
        geometry=[Point(-70.995, -30.004), Point(-70.994, -30.003), Point(-70.5, -29.5)],
        crs="EPSG:4326",
    )
    vias = gpd.GeoDataFrame(
        {"highway": ["primary", "primary"]},
        geometry=[
            LineString([(-71.0, -30.004), (-70.99, -30.004)]),  # cruza el polígono
            LineString([(-72.0, -30.0), (-71.9, -30.0)]),        # lejos, sin intersección
        ],
        crs="EPSG:4326",
    )
    servicios.to_file(ruta_data(cfg, "vector", "osm_servicios.gpkg"), driver="GPKG")
    vias.to_file(ruta_data(cfg, "vector", "osm_vias.gpkg"), driver="GPKG")

    destino = evaluar_exposicion(cfg, sufijo=sufijo)
    resumen = json.loads(destino.read_text())

    celda_ha = area_celda_m2(TRANSFORM, LAT_MEDIA) / 1e4
    assert resumen["urbano_ha"] == _area_urbana_ha(extension, worldcover, celda_ha)

    assert len(resumen["servicios"]) == 2  # el servicio lejano queda afuera
    nombres = {s["nombre"] for s in resumen["servicios"]}
    assert nombres == {"Hospital Test", "s/n"}  # el sin-nombre cae al valor por defecto

    assert resumen["vias_km"] > 0.0
    vias_expuestas = gpd.read_file(ruta_outputs(cfg, f"vias_expuestas_{sufijo}.geojson"))
    assert len(vias_expuestas) == 1  # solo la vía que cruza el polígono, no la lejana
