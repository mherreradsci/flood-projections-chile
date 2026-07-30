"""Poda de outputs/ (`limpieza`).

Funciones puras sobre nombres de archivo: se ejercitan creando HTML vacíos en
`tmp_path`, sin datos reales. El test que más importa es
`test_el_patron_parsea_lo_que_arma_ciclo_tag`: `PATRON_MAPA` es la dirección
inversa de `utils.ciclo_tag`, y son dos definiciones del mismo formato que
pueden separarse en silencio — si alguien cambia el formato del tag, la poda
dejaría de encontrar archivos y fallaría en silencio, no con un error.
"""

from datetime import datetime, timezone
from pathlib import Path

import pytest

from inundaciones.limpieza import (
    PATRON_MAPA,
    _raster_de_mapa,
    ciclos_excedentes,
    duplicados_por_ciclo,
)
from inundaciones.utils import ciclo_tag


def _crear(raiz, *nombres):
    for n in nombres:
        (raiz / n).write_text("<html></html>")
    return raiz


def _mapa(sufijo, ciclo, render):
    return f"mapa_anegamientos_{sufijo}_{ciclo}_{render}.html"


def _raster(sufijo, ciclo, render):
    return f"mapa_anegamientos_{sufijo}_extension_{ciclo}_{render}.tif"


class TestPatron:
    @pytest.mark.parametrize("ciclo_iso", [
        "2026-07-29T12:00:00+00:00",
        "2026-07-29T00:00:00+00:00",
        "2026-01-05T06:00:00+00:00",
    ])
    def test_el_patron_parsea_lo_que_arma_ciclo_tag(self, ciclo_iso):
        # se compone el nombre igual que mapa.generar_mapa, sin escribirlo a mano
        tag = ciclo_tag(ciclo_iso)
        nombre = f"mapa_anegamientos_gfs{tag}_20260729-153946.html"
        m = PATRON_MAPA.match(nombre)
        assert m is not None, f"PATRON_MAPA no parsea {nombre}"
        assert m["sufijo"] == "gfs"
        assert m["ciclo"] == tag.lstrip("_")
        assert m["render"] == "20260729-153946"

    def test_ignora_escenarios_sin_ciclo(self):
        # ciclo_tag devuelve "" para escenarios: el nombre no lleva ciclo
        assert ciclo_tag(None) == ""
        assert PATRON_MAPA.match(
            "mapa_anegamientos_extremo_200mm_20260728-224946.html") is None

    def test_ignora_el_formato_legado_sin_fecha(self):
        assert PATRON_MAPA.match(
            "mapa_anegamientos_ifs_12utc_20260716-184449.html") is None


class TestRasterDeMapa:
    def test_deriva_el_nombre_del_raster_pareado(self):
        # mismo tag sufijo/ciclo/render que arma mapa.generar_mapa, solo
        # cambia prefijo y extensión
        html = Path("/x") / _mapa("gfs", "20260729_18utc", "20260729-190059")
        assert _raster_de_mapa(html).name == _raster(
            "gfs", "20260729_18utc", "20260729-190059")


class TestDuplicadosPorCiclo:
    def test_deja_el_render_mas_nuevo_de_cada_ciclo(self, tmp_path):
        _crear(tmp_path,
               _mapa("gfs", "20260729_12utc", "20260729-130107"),
               _mapa("gfs", "20260729_12utc", "20260729-153946"),
               _mapa("gfs", "20260729_12utc", "20260729-144649"))
        sobran = [f.name for f in duplicados_por_ciclo(tmp_path)]
        assert len(sobran) == 2
        assert _mapa("gfs", "20260729_12utc", "20260729-153946") not in sobran

    def test_no_toca_ciclos_con_un_solo_render(self, tmp_path):
        _crear(tmp_path,
               _mapa("gfs", "20260729_12utc", "20260729-153946"),
               _mapa("gfs", "20260729_06utc", "20260729-070104"))
        assert duplicados_por_ciclo(tmp_path) == []

    def test_no_mezcla_fuentes_del_mismo_ciclo(self, tmp_path):
        # gfs e ifs del mismo ciclo son resultados distintos, no duplicados
        _crear(tmp_path,
               _mapa("gfs", "20260717_12utc", "20260717-130858"),
               _mapa("ifs", "20260717_12utc", "20260717-234909"))
        assert duplicados_por_ciclo(tmp_path) == []

    def test_deja_intactos_escenarios_y_legado(self, tmp_path):
        _crear(tmp_path,
               "mapa_anegamientos_extremo_200mm_20260728-224946.html",
               "mapa_anegamientos_ifs_12utc_20260716-184449.html",
               _mapa("gfs", "20260729_12utc", "20260729-130107"),
               _mapa("gfs", "20260729_12utc", "20260729-153946"))
        sobran = [f.name for f in duplicados_por_ciclo(tmp_path)]
        assert sobran == [_mapa("gfs", "20260729_12utc", "20260729-130107")]

    def test_carpeta_vacia(self, tmp_path):
        assert duplicados_por_ciclo(tmp_path) == []

    def test_arrastra_el_raster_pareado_del_render_sobrante(self, tmp_path):
        viejo, nuevo = "20260729-130107", "20260729-153946"
        _crear(tmp_path,
               _mapa("gfs", "20260729_12utc", viejo),
               _mapa("gfs", "20260729_12utc", nuevo),
               _raster("gfs", "20260729_12utc", viejo),
               _raster("gfs", "20260729_12utc", nuevo))
        sobran = {f.name for f in duplicados_por_ciclo(tmp_path)}
        assert sobran == {_mapa("gfs", "20260729_12utc", viejo),
                          _raster("gfs", "20260729_12utc", viejo)}

    def test_no_falla_si_el_raster_pareado_no_existe(self, tmp_path):
        # el raster puede faltar (corrida vieja, anterior a este cambio);
        # la poda del HTML no debe depender de que exista
        _crear(tmp_path,
               _mapa("gfs", "20260729_12utc", "20260729-130107"),
               _mapa("gfs", "20260729_12utc", "20260729-153946"))
        sobran = [f.name for f in duplicados_por_ciclo(tmp_path)]
        assert sobran == [_mapa("gfs", "20260729_12utc", "20260729-130107")]


class TestCiclosExcedentes:
    def test_conserva_los_n_ciclos_mas_recientes(self, tmp_path):
        for c in ("20260727_00utc", "20260728_00utc", "20260729_00utc"):
            _crear(tmp_path, _mapa("gfs", c, "20260729-010101"))
        sobran = [f.name for f in ciclos_excedentes(tmp_path, 2)]
        assert sobran == [_mapa("gfs", "20260727_00utc", "20260729-010101")]

    def test_cuenta_ciclos_y_no_archivos(self, tmp_path):
        # el ciclo viejo tiene 3 renders; conservar=1 debe borrar los 3
        for r in ("20260728-010101", "20260728-020202", "20260728-030303"):
            _crear(tmp_path, _mapa("gfs", "20260728_00utc", r))
        _crear(tmp_path, _mapa("gfs", "20260729_00utc", "20260729-010101"))
        assert len(ciclos_excedentes(tmp_path, 1)) == 3

    def test_cada_fuente_conserva_su_propia_cuota(self, tmp_path):
        for c in ("20260728_00utc", "20260729_00utc"):
            _crear(tmp_path, _mapa("gfs", c, "20260729-010101"))
        _crear(tmp_path, _mapa("ifs", "20260717_12utc", "20260717-234909"))
        sobran = [f.name for f in ciclos_excedentes(tmp_path, 1)]
        # el único ciclo ifs sobrevive aunque sea el más viejo en absoluto
        assert sobran == [_mapa("gfs", "20260728_00utc", "20260729-010101")]

    def test_conservar_cero_o_negativo_no_borra_nada(self, tmp_path):
        _crear(tmp_path, _mapa("gfs", "20260729_00utc", "20260729-010101"))
        assert ciclos_excedentes(tmp_path, 0) == []
        assert ciclos_excedentes(tmp_path, -1) == []

    def test_no_borra_si_hay_menos_ciclos_que_la_cuota(self, tmp_path):
        _crear(tmp_path, _mapa("gfs", "20260729_00utc", "20260729-010101"))
        assert ciclos_excedentes(tmp_path, 8) == []


def test_ciclo_tag_ordena_cronologicamente():
    """La poda ordena ciclos como texto; debe coincidir con el orden temporal."""
    tags = [ciclo_tag(datetime(2026, 7, d, h, tzinfo=timezone.utc).isoformat())
            for d, h in [(28, 0), (28, 18), (29, 6), (29, 12)]]
    assert tags == sorted(tags)
