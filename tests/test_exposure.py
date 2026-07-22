import geopandas as gpd
import numpy as np
from shapely.geometry import LineString, box

from inundaciones.exposure import _area_urbana_ha, _vias_expuestas


def test_sin_interseccion_urbano_da_cero():
    ext = np.array([[1, 0], [0, 1]])
    lc = np.array([[10, 50], [50, 10]])  # urbano donde no hay anegamiento
    assert _area_urbana_ha(ext, lc, celda_ha=1.0) == 0.0


def test_cuenta_solo_celdas_anegadas_y_urbanas():
    ext = np.array([[1, 1], [0, 1]])
    lc = np.array([[50, 10], [50, 50]])  # urbano: (0,0),(1,0),(1,1); anegado: (0,0),(0,1),(1,1)
    # intersección: (0,0) y (1,1) -> 2 celdas
    assert _area_urbana_ha(ext, lc, celda_ha=2.5) == 5.0


def test_escala_linealmente_con_el_area_de_celda():
    ext = np.ones((3, 3), dtype="uint8")
    lc = np.full((3, 3), 50)
    assert _area_urbana_ha(ext, lc, celda_ha=1.0) == 9.0
    assert _area_urbana_ha(ext, lc, celda_ha=0.5) == 4.5


def test_vias_expuestas_mide_solo_lo_que_intersecta_el_poligono():
    poligono = box(-71.01, -30.01, -70.99, -29.99)
    dentro = LineString([(-71.005, -30.0), (-70.995, -30.0)])
    fuera = LineString([(-72.0, -30.0), (-71.9, -30.0)])
    vias = gpd.GeoDataFrame({"highway": ["primary", "primary"]},
                            geometry=[dentro, fuera], crs="EPSG:4326")
    afectadas, km = _vias_expuestas(vias, poligono)
    assert len(afectadas) == 1
    assert km > 0.0


def test_sin_vias_dentro_del_poligono_da_cero_km():
    poligono = box(-71.01, -30.01, -70.99, -29.99)
    fuera = LineString([(-72.0, -30.0), (-71.9, -30.0)])
    vias = gpd.GeoDataFrame({"highway": ["primary"]}, geometry=[fuera], crs="EPSG:4326")
    afectadas, km = _vias_expuestas(vias, poligono)
    assert afectadas.empty
    assert km == 0.0
