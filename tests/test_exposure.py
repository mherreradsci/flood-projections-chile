import numpy as np

from inundaciones.exposure import _area_urbana_ha


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
