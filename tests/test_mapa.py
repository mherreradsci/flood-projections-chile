"""Tests de las funciones puras de armado de leyendas (`mapa`).

No tocan folium ni rasters: solo verifican que la barra de color describa lo
mismo que dibuja `_overlay` (colormap, alfa y marcas), que es el invariante que
se rompe en silencio — la leyenda queda linda pero miente sobre la capa.
"""

import re

import numpy as np
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


class TestEstiloClases:
    def test_una_entrada_por_clase(self):
        colores, alfas = mapa._estilo_clases()
        assert len(colores) == len(mapa.PRECIPITACION_CLASES)
        assert len(alfas) == len(mapa.PRECIPITACION_CLASES)

    def test_alfa_de_la_clase_mas_baja_es_legible(self):
        # el punto de las clases: una banda baja se ve aunque en otra región
        # llueva 20 veces más. Con la rampa continua anterior daba ~0.01
        _, alfas = mapa._estilo_clases()
        assert alfas[0] == pytest.approx(mapa.PRECIPITACION_ALFA_MIN)
        assert alfas[0] >= 0.3
        assert alfas[-1] == pytest.approx(mapa.PRECIPITACION_OPACIDAD)

    def test_no_arranca_en_el_blanco_del_colormap(self):
        colores, _ = mapa._estilo_clases()
        # la clase más baja debe tener color propio, no ser casi blanca
        r, g, b, _ = colores[0]
        assert min(r, g, b) < 0.92

    def test_colores_distintos_entre_clases(self):
        colores, _ = mapa._estilo_clases()
        assert len({c[:3] for c in colores}) == len(colores)


class TestParadasClases:
    def test_bloques_macizos_de_igual_ancho(self):
        colores, alfas = mapa._estilo_clases()
        css = mapa._paradas_clases(colores, alfas)
        assert css.count("rgba(") == len(colores)
        # cortes duros: cada tramo declara inicio y fin, sin interpolar
        assert css.count("%") == 2 * len(colores)

    def test_cubre_de_0_a_100(self):
        colores, alfas = mapa._estilo_clases()
        css = mapa._paradas_clases(colores, alfas)
        assert " 0.0000%" in css
        assert "100.0000%" in css


class TestLeyenda:
    def test_incluye_todas_las_marcas(self):
        html = mapa._leyenda("x", "T", "red 0% 100%", ["1", "5", "15"])
        assert ">1<" in html and ">5<" in html and ">15<" in html

    def test_modo_define_la_clase_css_de_las_marcas(self):
        ext = mapa._leyenda("x", "T", "red 0% 100%", ["1", "2", "3"])
        cls = mapa._leyenda("x", "T", "red 0% 100%", ["1", "2"], modo="clases")
        assert "leyenda-marcas--extremos" in ext
        assert "leyenda-marcas--clases" in cls

    def test_mostrar_false_arranca_oculta(self):
        oculta = mapa._leyenda("x", "T", "red 0% 100%", ["1"], mostrar=False)
        visible = mapa._leyenda("x", "T", "red 0% 100%", ["1"])
        assert "display:none" in oculta
        assert "display:none" not in visible

    def test_id_y_titulo_se_propagan(self):
        html = mapa._leyenda("leyenda-precipitacion", "Precipitación (72 h, mm)",
                             "red 0% 100%", ["1"])
        assert "id='leyenda-precipitacion'" in html
        assert "Precipitación (72 h, mm)" in html

    def test_nota_opcional_va_como_title(self):
        sin_nota = mapa._leyenda("x", "T", "red 0% 100%", ["1"])
        con_nota = mapa._leyenda("x", "T", "red 0% 100%", ["1"],
                                 nota="escala fija")
        assert "title=" not in sin_nota
        assert "title='escala fija'" in con_nota


class TestCoherenciaConElOverlay:
    """Las constantes que comparten overlay y leyenda no deben divergir."""

    def test_ids_del_sync_existen_en_los_estilos(self):
        css = mapa._estilos_leyendas()
        assert "#leyendas-mapa" in css
        assert ".leyenda-barra" in css

    @pytest.mark.parametrize("mm,clase", [
        (0.1, -1),    # bajo el primer corte: transparente
        (0.9, -1),
        (1.0, 0),     # el corte es el piso de su clase
        (4.9, 0),
        (15.6, 2),    # Atacama, GFS ciclo 12 del 29-07-2026
        (42.2, 3),    # Coquimbo, mismo ciclo → clase distinta, como debe ser
        (100.0, 4),   # escenario extremo_100mm
        (315.9, 5),   # IFS: cae en la clase abierta
    ])
    def test_los_cortes_separan_los_casos_reales(self, mm, clase):
        # es la misma cuenta que hace _overlay; si los cortes se tocan sin
        # mirar, dos regiones muy distintas pueden caer en la misma banda
        assert np.digitize(mm, mapa.PRECIPITACION_CLASES) - 1 == clase

    def test_hay_una_marca_por_clase(self):
        colores, _ = mapa._estilo_clases()
        assert len(colores) == len(mapa.PRECIPITACION_CLASES)


class TestResponsive:
    """En móvil las leyendas siguen al selector de capas."""

    def test_la_regla_colapsado_vive_dentro_del_media_query(self):
        css = mapa._estilos_responsive()
        cuerpo = css.split("@media (max-width: 767px) {")[1]
        # fuera del media query escondería las leyendas también en desktop
        assert "#leyendas-mapa.colapsado { display: none; }" in cuerpo

    def test_cada_cambio_de_estado_del_control_sincroniza(self):
        # el invariante: si alguien agrega otro punto que toca
        # leaflet-control-layers-expanded y olvida sincronizar, las barras
        # quedan visibles con el selector cerrado (o al revés)
        js = mapa._estilos_responsive().split("<script>")[1]
        cambios = (js.count("classList.remove('leaflet-control-layers-expanded')")
                   + js.count("classList.add('leaflet-control-layers-expanded')"))
        assert cambios == js.count("sincronizarLeyendas();")

    def test_el_estado_se_deriva_del_control_y_no_de_una_bandera(self):
        js = mapa._estilos_responsive()
        assert "classList.contains('leaflet-control-layers-expanded')" in js

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
