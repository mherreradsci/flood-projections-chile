"""Test de integración de calcular_escorrentia: rasters/vectores sintéticos
en tmp_path, sin red. Ver tests/test_flood_model_integracion.py para el
porqué de esta categoría (documentada en CLAUDE.md).

obtener_subcuencas() cachea por existencia de archivo (data/vector/
subcuencas.geojson): precrearlo evita por completo la descarga de
HydroBASINS. rasterizar_subcuencas() corre de verdad (no se cachea acá)
contra un dem.tif sintético, así que también queda ejercitada.
"""

import geopandas as gpd
import numpy as np
import pytest
from rasterio.transform import Affine
from shapely.geometry import box

from inundaciones.runoff import _escorrentia_mm, calcular_escorrentia
from inundaciones.utils import area_celda_m2, guardar_raster, ruta_data

TRANSFORM = Affine(0.001, 0.0, -71.0, 0.0, -0.001, -30.0)
LAT_MEDIA = -30.0


def _cfg(tmp_path):
    return {
        "rutas": {"data": str(tmp_path / "data"), "outputs": str(tmp_path / "outputs")},
        "region": {"bbox": [-71.01, -30.02, -70.99, -29.98]},  # lat_media = -30.0
        "modelo": {"banda_nieve_humeda_m": 500.0, "factor_volumen_defecto": 1.0},
    }


def test_filtra_por_isoterma_y_promedia_solo_las_celdas_pluviales(tmp_path):
    cfg = _cfg(tmp_path)

    # dos subcuencas mitad izquierda / mitad derecha de una grilla 10x10
    sub1 = box(-71.0, -30.01, -70.995, -30.0)   # cols 0-4 -> id_raster 1
    sub2 = box(-70.995, -30.01, -70.99, -30.0)  # cols 5-9 -> id_raster 2
    sub = gpd.GeoDataFrame({"HYBAS_ID": [10, 20]}, geometry=[sub1, sub2], crs="EPSG:4326")
    sub.to_file(ruta_data(cfg, "vector", "subcuencas.geojson"), driver="GeoJSON")

    # sub1: toda baja (pluvial). sub2: mitad superior baja (pluvial), mitad
    # inferior alta (sobre la isoterma -> excluida)
    dem = np.full((10, 10), 100.0, dtype="float32")
    dem[5:10, 5:10] = 3000.0
    guardar_raster(tmp_path / "data" / "dem" / "dem.tif", dem, TRANSFORM, nodata=-9999)

    # CN distinto en la mitad no-pluvial de sub2, para probar que se excluye del promedio
    cn = np.full((10, 10), 70.0, dtype="float32")
    cn[0:5, 5:10] = 80.0
    cn[5:10, 5:10] = 90.0
    guardar_raster(tmp_path / "data" / "landcover" / "curve_number.tif", cn, TRANSFORM,
                   nodata=-9999)

    # lluvia disparatada en la zona no-pluvial de sub2: si el promedio la
    # incluyera, P_mm quedaría muy lejos de 40
    precip = np.full((10, 10), 40.0, dtype="float32")
    precip[5:10, 5:10] = 999.0
    guardar_raster(tmp_path / "data" / "forecast" / "precip_mm_test.tif", precip, TRANSFORM,
                   nodata=-9999)

    df = calcular_escorrentia(cfg, isoterma_m=2000.0, sufijo="test").set_index("HYBAS_ID")

    celda_m2 = area_celda_m2(TRANSFORM, LAT_MEDIA)

    sub1_fila = df.loc[10]
    assert sub1_fila.frac_pluvial == 1.0
    assert sub1_fila.P_mm == pytest.approx(40.0)
    assert sub1_fila.CN == pytest.approx(70.0)
    q1 = _escorrentia_mm(40.0, 70.0)
    assert sub1_fila.Q_mm == pytest.approx(q1)
    assert sub1_fila.volumen_m3 == pytest.approx(q1 / 1000.0 * 50 * celda_m2)

    sub2_fila = df.loc[20]
    assert sub2_fila.frac_pluvial == pytest.approx(0.5)
    assert sub2_fila.P_mm == pytest.approx(40.0)  # excluye los 999 de la mitad no pluvial
    assert sub2_fila.CN == pytest.approx(80.0)    # excluye el 90 de la mitad no pluvial
    q2 = _escorrentia_mm(40.0, 80.0)
    assert sub2_fila.Q_mm == pytest.approx(q2)
    assert sub2_fila.volumen_m3 == pytest.approx(q2 / 1000.0 * 25 * celda_m2)
