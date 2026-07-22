import geopandas as gpd
import numpy as np
import pytest
from rasterio.transform import Affine
from shapely.geometry import box

from inundaciones.new_areas import _filtrar_por_area_minima, _vectorizar

TRANSFORM = Affine(0.001, 0.0, -71.0, 0.0, -0.001, -30.0)


def test_mascara_vacia_da_geodataframe_vacio():
    mascara = np.zeros((5, 5), dtype=bool)
    gdf = _vectorizar(mascara, TRANSFORM)
    assert gdf.empty
    assert gdf.crs == "EPSG:4326"


def test_un_bloque_da_un_solo_poligono():
    mascara = np.zeros((10, 10), dtype=bool)
    mascara[2:5, 2:5] = True
    gdf = _vectorizar(mascara, TRANSFORM)
    assert len(gdf) == 1
    # área nominal del bloque: 3x3 celdas de 0.001° -> en grados²
    area_esperada_deg2 = (3 * 0.001) * (3 * 0.001)
    # .area en la geometría shapely directamente: gdf.geometry.area advierte
    # (con razón) que medir área en grados sobre un CRS geográfico sesga con
    # la latitud; aquí es intencional, solo se verifica la geometría vectorizada.
    assert gdf.geometry.iloc[0].area == pytest.approx(area_esperada_deg2, rel=1e-6)


def test_dos_bloques_disjuntos_dan_dos_poligonos():
    mascara = np.zeros((10, 10), dtype=bool)
    mascara[0:2, 0:2] = True
    mascara[7:9, 7:9] = True
    gdf = _vectorizar(mascara, TRANSFORM)
    assert len(gdf) == 2


def test_filtra_poligonos_bajo_el_area_minima():
    # a esta latitud, "grande" mide muchos km²; "chico", una fracción de m²
    grande = box(-71.02, -30.02, -70.98, -29.98)
    chico = box(-71.0001, -30.0001, -70.9999, -29.9999)
    gdf = gpd.GeoDataFrame({"nombre": ["grande", "chico"]},
                           geometry=[grande, chico], crs="EPSG:4326")
    filtrado = _filtrar_por_area_minima(gdf, celda_km2=0.01)  # umbral: 4*0.01 = 0.04 km²
    assert list(filtrado.nombre) == ["grande"]


def test_gdf_vacio_se_devuelve_sin_cambios():
    gdf = gpd.GeoDataFrame(geometry=[], crs="EPSG:4326")
    filtrado = _filtrar_por_area_minima(gdf, celda_km2=0.01)
    assert filtrado.empty
    assert "area_km2" not in filtrado.columns
