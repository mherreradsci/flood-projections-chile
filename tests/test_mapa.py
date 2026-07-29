"""Tests de las funciones puras de armado de leyendas (`mapa`).

No tocan folium ni rasters: solo verifican que la barra de color describa lo
mismo que dibuja `_overlay` (colormap, alfa y marcas), que es el invariante que
se rompe en silencio — la leyenda queda linda pero miente sobre la capa.
"""

import re

import pytest

from inundaciones import mapa


def _alfas(paradas):
    return [float(re.search(r",([\d.]+)\)", p).group(1)) for p in paradas]


class TestParadasGradiente:
    def test_extremos_reproducen_el_alfa_del_overlay(self):
        paradas = mapa._paradas_gradiente("YlGnBu", 0.22, 0.65)
        alfas = _alfas(paradas)
        assert alfas[0] == pytest.approx(0.22, abs=0.005)
        assert alfas[-1] == pytest.approx(0.65, abs=0.005)

    def test_alfa_crece_de_forma_monotona(self):
        alfas = _alfas(mapa._paradas_gradiente("Blues", 0.0, 0.7))
        assert alfas == sorted(alfas)

    def test_posiciones_cubren_0_a_100(self):
        paradas = mapa._paradas_gradiente("Blues", 0.0, 0.7, n=5)
        assert len(paradas) == 5
        assert paradas[0].endswith(" 0%")
        assert paradas[-1].endswith(" 100%")

    def test_colormap_distinto_da_colores_distintos(self):
        azul = mapa._paradas_gradiente("Blues", 0.0, 0.7)[-1]
        verde = mapa._paradas_gradiente("YlGnBu", 0.0, 0.7)[-1]
        assert azul != verde


class TestFmtMm:
    @pytest.mark.parametrize("valor,esperado", [
        (62.24000549316406, "62"),   # ruido de np.nanmax
        (11.45, "11"),
        (31.12, "31"),
        (10.0, "10"),
        (9.99, "10.0"),              # bajo el corte: un decimal
        (5.725, "5.7"),
        (1.0, "1.0"),
        (0.0, "0.0"),
    ])
    def test_ancho_fijo_segun_magnitud(self, valor, esperado):
        assert mapa._fmt_mm(valor) == esperado


class TestLeyenda:
    def test_incluye_las_tres_marcas(self):
        html = mapa._leyenda("x", "T", "Blues", 0.0, 0.7, ("1.0", "31", "62"))
        assert ">1.0<" in html and ">31<" in html and ">62<" in html

    def test_mostrar_false_arranca_oculta(self):
        oculta = mapa._leyenda("x", "T", "Blues", 0.0, 0.7,
                               ("1", "2", "3"), mostrar=False)
        visible = mapa._leyenda("x", "T", "Blues", 0.0, 0.7, ("1", "2", "3"))
        assert "display:none" in oculta
        assert "display:none" not in visible

    def test_id_y_titulo_se_propagan(self):
        html = mapa._leyenda("leyenda-precipitacion", "Precipitación (72 h, mm)",
                             "Blues", 0.0, 0.7, ("1", "2", "3"))
        assert "id='leyenda-precipitacion'" in html
        assert "Precipitación (72 h, mm)" in html

    def test_nota_opcional_va_como_title(self):
        sin_nota = mapa._leyenda("x", "T", "Blues", 0.0, 0.7, ("1", "2", "3"))
        con_nota = mapa._leyenda("x", "T", "Blues", 0.0, 0.7, ("1", "2", "3"),
                                 nota="escala local")
        assert "title=" not in sin_nota
        assert "title='escala local'" in con_nota


class TestCoherenciaConElOverlay:
    """Las constantes que comparten overlay y leyenda no deben divergir."""

    def test_ids_del_sync_existen_en_los_estilos(self):
        css = mapa._estilos_leyendas()
        assert "#leyendas-mapa" in css
        assert ".leyenda-barra" in css

    def test_umbral_precipitacion_es_el_piso_de_la_barra(self):
        # la marca izquierda rotula el umbral bajo el cual no se pinta nada
        assert mapa._fmt_mm(mapa.PRECIPITACION_UMBRAL) == "1.0"

    def test_barra_y_marcas_comparten_el_ancho(self):
        # escribir el ancho dos veces deja la marca derecha pasada del fin del
        # degradado: la fila de marcas la estira el título, más largo que la
        # barra. Ambas deben leer la misma custom property.
        css = mapa._estilos_leyendas()
        assert "--ancho-barra:" in css
        assert css.count("width: var(--ancho-barra)") == 2

    def test_marcas_en_tercios_y_no_space_between(self):
        # space-between descentra la marca del medio cuando las etiquetas
        # tienen ancho distinto ('1.0' vs '16')
        css = mapa._estilos_leyendas()
        assert "justify-content: space-between" not in css
        assert "flex: 1 1 0" in css
