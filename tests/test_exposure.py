import geopandas as gpd
import numpy as np
import pytest
from shapely.geometry import LineString, MultiPolygon, box

from inundaciones.exposure import _area_urbana_ha, _partes, _vias_expuestas


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


# --- recorte contra máscara multi-parte ---------------------------------------
# `_vias_expuestas` recorta contra las partes del polígono por separado y no
# contra la unión, porque el bbox de una máscara de anegamiento cubre la región
# entera e inutiliza el prefiltro del índice espacial. Estos tests fijan que esa
# optimización no cambie el resultado.


def test_partes_de_un_poligono_simple_es_el_mismo():
    p = box(0, 0, 1, 1)
    assert _partes(p) == [p]


def test_partes_de_un_multipoligono_los_separa():
    a, b = box(0, 0, 1, 1), box(5, 5, 6, 6)
    assert len(_partes(MultiPolygon([a, b]))) == 2


def test_mascara_multiparte_da_lo_mismo_que_la_union():
    """El resultado no debe depender de si la máscara viene unida o en partes."""
    a = box(-71.01, -30.01, -70.99, -29.99)
    b = box(-70.51, -30.01, -70.49, -29.99)   # disjunta de `a`
    via_a = LineString([(-71.005, -30.0), (-70.995, -30.0)])
    via_b = LineString([(-70.505, -30.0), (-70.495, -30.0)])
    vias = gpd.GeoDataFrame({"highway": ["primary", "secondary"]},
                            geometry=[via_a, via_b], crs="EPSG:4326")

    _, km_multi = _vias_expuestas(vias, MultiPolygon([a, b]))
    _, km_union = _vias_expuestas(vias, gpd.GeoSeries([a, b]).union_all())
    assert km_multi == km_union > 0.0


def test_una_via_que_cruza_dos_partes_no_se_cuenta_dos_veces():
    """Partes disjuntas: la suma por parte debe igualar el largo recortado."""
    izq = box(-71.00, -30.01, -70.98, -29.99)
    der = box(-70.98, -30.01, -70.96, -29.99)   # comparten el borde -70.98
    # la vía cruza ambas cajas de lado a lado
    via = LineString([(-70.995, -30.0), (-70.965, -30.0)])
    vias = gpd.GeoDataFrame({"highway": ["primary"]}, geometry=[via], crs="EPSG:4326")

    _, km_partes = _vias_expuestas(vias, MultiPolygon([izq, der]))
    _, km_entero = _vias_expuestas(vias, box(-71.00, -30.01, -70.96, -29.99))
    assert km_partes == pytest.approx(km_entero, abs=0.05)


def test_conserva_los_atributos_de_las_vias():
    """El llamador guarda las vías afectadas como capa; no debe perder columnas."""
    poligono = box(-71.01, -30.01, -70.99, -29.99)
    dentro = LineString([(-71.005, -30.0), (-70.995, -30.0)])
    vias = gpd.GeoDataFrame({"highway": ["motorway"]}, geometry=[dentro],
                            crs="EPSG:4326")
    afectadas, _ = _vias_expuestas(vias, poligono)
    assert "highway" in afectadas.columns
    assert afectadas.iloc[0]["highway"] == "motorway"


def test_sin_vias_devuelve_cero_sin_calcular():
    vacias = gpd.GeoDataFrame({"highway": []}, geometry=[], crs="EPSG:4326")
    afectadas, km = _vias_expuestas(vacias, box(0, 0, 1, 1))
    assert afectadas.empty
    assert km == 0.0
