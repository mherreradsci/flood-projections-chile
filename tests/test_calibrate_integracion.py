"""Test de integración de calibrar: rasters/vectores sintéticos en tmp_path,
sin red. Ver tests/test_flood_model_integracion.py para el porqué de esta
categoría (documentada en CLAUDE.md).

Es el más pesado de los cuatro candidatos de integración porque calibrar()
llama a calcular_escorrentia() internamente, así que necesita todos los
insumos de esa cadena (subcuencas.geojson, dem.tif, curve_number.tif) más
los propios (hand.tif, subcuencas_id.tif vía caché, huella_<evento>.tif).
"""

import json

import geopandas as gpd
import numpy as np
import pandas as pd
import pytest
from rasterio.transform import Affine
from shapely.geometry import box

from inundaciones.calibrate import calibrar, cargar_factores
from inundaciones.utils import guardar_raster, ruta_data, ruta_outputs

TRANSFORM = Affine(0.001, 0.0, -71.0, 0.0, -0.001, -30.0)
MIN_OBS = 20  # debe coincidir con calibrate.MIN_OBS


def _cfg(tmp_path):
    return {
        "rutas": {"data": str(tmp_path / "data"), "outputs": str(tmp_path / "outputs")},
        "region": {"bbox": [-71.01, -30.02, -70.99, -29.98]},  # lat_media = -30.0
        "terreno": {"hand_max_m": 10.0},
        "modelo": {"banda_nieve_humeda_m": 500.0, "factor_volumen_defecto": 1.0},
        # calcular_escorrentia calcula un sufijo por defecto aunque nunca lea
        # data/forecast (precip_mm/isoterma0_m se fuerzan desde el evento)
        "pronostico": {"modelo": "no-usado"},
        "calibracion": {
            "factor_min": 0.5, "factor_max": 5.0,
            "eventos": [
                {"nombre": "evento1", "precipitacion_mm": 40.0, "isoterma0_m": 3000.0},
            ],
        },
    }


def test_calibrar_deriva_factor_y_hereda_mediana_en_subcuenca_sin_observacion(tmp_path):
    cfg = _cfg(tmp_path)

    # dos subcuencas mitad izquierda / mitad derecha, igual que en
    # test_runoff_integracion.py
    sub1 = box(-71.0, -30.01, -70.995, -30.0)   # cols 0-4 -> id_raster 1, HYBAS_ID 10
    sub2 = box(-70.995, -30.01, -70.99, -30.0)  # cols 5-9 -> id_raster 2, HYBAS_ID 20
    sub = gpd.GeoDataFrame({"HYBAS_ID": [10, 20]}, geometry=[sub1, sub2], crs="EPSG:4326")
    sub.to_file(ruta_data(cfg, "vector", "subcuencas.geojson"), driver="GeoJSON")

    # terreno bajo en toda la grilla -> con isoterma 3000m ambas subcuencas
    # quedan 100% pluviales (no es lo que se está probando acá)
    dem = np.full((10, 10), 100.0, dtype="float32")
    guardar_raster(tmp_path / "data" / "dem" / "dem.tif", dem, TRANSFORM, nodata=-9999)
    cn = np.full((10, 10), 70.0, dtype="float32")
    guardar_raster(tmp_path / "data" / "landcover" / "curve_number.tif", cn, TRANSFORM,
                   nodata=-9999)

    # HAND crece con la fila (0..9), igual en ambas columnas de subcuenca
    hand = np.tile(np.arange(10, dtype="float32").reshape(10, 1), (1, 10))
    guardar_raster(tmp_path / "data" / "dem" / "hand.tif", hand, TRANSFORM, nodata=-9999)

    # huella observada: 25 celdas en subcuenca 1 (>= MIN_OBS), 10 en subcuenca 2 (< MIN_OBS)
    huella = np.zeros((10, 10), dtype="uint8")
    huella[0:5, 0:5] = 1
    huella[0:2, 5:10] = 1
    guardar_raster(tmp_path / "data" / "historical" / "huella_evento1.tif", huella, TRANSFORM,
                   nodata=255, dtype="uint8")

    destino = calibrar(cfg)
    assert destino == ruta_data(cfg, "calibracion.json")
    factores = json.loads(destino.read_text())

    reporte = pd.read_csv(ruta_outputs(cfg, "calibracion_reporte.csv"))
    fila_10 = reporte[reporte.HYBAS_ID == 10].iloc[0]
    fila_20 = reporte[reporte.HYBAS_ID == 20].iloc[0]
    assert fila_10.n_obs == 25 and fila_10.n_obs >= MIN_OBS
    assert fila_20.n_obs == 10 and fila_20.n_obs < MIN_OBS
    assert not pd.isna(fila_10.factor)  # observación suficiente: factor real
    assert pd.isna(fila_20.factor)      # insuficiente: se calibra como NaN

    assert set(factores) == {"10", "20"}
    assert cfg["calibracion"]["factor_min"] <= factores["10"] <= cfg["calibracion"]["factor_max"]
    # única subcuenca con observación suficiente -> la mediana regional ES su
    # propio factor, y subcuenca 20 debe heredar exactamente ese valor
    assert factores["20"] == pytest.approx(factores["10"])

    assert cargar_factores(cfg) == factores
