import pytest

from inundaciones.runoff import _escorrentia_mm


def test_precipitacion_bajo_la_abstraccion_no_genera_escorrentia():
    # CN=80 -> S=63.5mm, Ia=12.7mm; con 10mm de lluvia no se supera Ia
    assert _escorrentia_mm(P=10.0, CN=80.0) == 0.0


def test_cn_100_toda_la_lluvia_escurre():
    # superficie impermeable (CN=100): S=0, Ia=0, toda la lluvia es escorrentía
    assert _escorrentia_mm(P=50.0, CN=100.0) == pytest.approx(50.0)


def test_escorrentia_no_supera_la_precipitacion():
    Q = _escorrentia_mm(P=50.0, CN=70.0)
    assert 0.0 <= Q <= 50.0


def test_escorrentia_crece_con_la_precipitacion():
    Q_bajo = _escorrentia_mm(P=30.0, CN=75.0)
    Q_alto = _escorrentia_mm(P=100.0, CN=75.0)
    assert Q_alto > Q_bajo
