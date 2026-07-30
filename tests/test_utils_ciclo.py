"""Nombres de mapa por ciclo: `ciclo_tag` (puro) y `mapas_de_ciclo` (glob en tmp_path).

`mapas_de_ciclo` es lo que usa `scripts/reprocesar_ciclo_gfs.py` para no
reprocesar un ciclo que ya tiene mapa; si el patrón se desincroniza del
nombre que escribe `mapa.generar_mapa`, el detector deja de ver duplicados
en silencio. Por eso se prueba contra archivos con el nombre real.
"""

import pytest

from inundaciones.utils import ciclo_tag, mapas_de_ciclo


@pytest.mark.parametrize("iso,esperado", [
    ("2026-07-18T06:00:00+00:00", "_20260718_06utc"),
    ("2026-07-18T06:00:00", "_20260718_06utc"),   # sin tz explícita
    ("2026-07-23T00:00:00+00:00", "_20260723_00utc"),  # medianoche no se pierde
    ("2026-12-01T18:00:00+00:00", "_20261201_18utc"),
])
def test_ciclo_tag_formatea_fecha_y_hora(iso, esperado):
    assert ciclo_tag(iso) == esperado


@pytest.mark.parametrize("vacio", [None, ""])
def test_ciclo_tag_vacio_sin_ciclo(vacio):
    """Escenario sintético: sin ciclo no hay tag (y no hay duplicado que buscar)."""
    assert ciclo_tag(vacio) == ""


def _cfg(tmp_path):
    return {"rutas": {"data": "data", "outputs": "outputs"}, "region": {"id": "prueba"}}


def _preparar(tmp_path, monkeypatch, nombres, carpeta="gfs"):
    """Crea outputs/prueba/<carpeta>/<nombres> bajo tmp_path y apunta RAIZ ahí.

    `carpeta` es la subcarpeta por fuente (gfs/ifs/escenario, ver
    `utils.ruta_salida`); todos los nombres de este módulo son mapas gfs.
    """
    monkeypatch.setattr("inundaciones.utils.RAIZ", tmp_path)
    destino = tmp_path / "outputs" / "prueba" / carpeta
    destino.mkdir(parents=True)
    for n in nombres:
        (destino / n).write_text("<html></html>")
    return destino


def test_mapas_de_ciclo_encuentra_renders_del_mismo_ciclo(tmp_path, monkeypatch):
    """Dos renders del mismo ciclo = el duplicado que el chequeo debe detectar."""
    _preparar(tmp_path, monkeypatch, [
        "mapa_anegamientos_gfs_20260718_06utc_20260718-100049.html",
        "mapa_anegamientos_gfs_20260718_06utc_20260718-114903.html",
    ])
    hallados = mapas_de_ciclo(_cfg(tmp_path), "gfs", "2026-07-18T06:00:00+00:00")
    assert [p.name for p in hallados] == [
        "mapa_anegamientos_gfs_20260718_06utc_20260718-100049.html",
        "mapa_anegamientos_gfs_20260718_06utc_20260718-114903.html",
    ]


def test_mapas_de_ciclo_no_confunde_otros_ciclos_ni_fuentes(tmp_path, monkeypatch):
    """No debe mezclar otro ciclo, otra hora del mismo día ni otra fuente."""
    _preparar(tmp_path, monkeypatch, [
        "mapa_anegamientos_gfs_20260718_06utc_20260718-100049.html",
        "mapa_anegamientos_gfs_20260718_12utc_20260718-155629.html",  # otra hora
        "mapa_anegamientos_gfs_20260719_06utc_20260719-071729.html",  # otro día
    ])
    # otra fuente: vive en su propia subcarpeta (ver ruta_salida)
    _preparar(tmp_path, monkeypatch, [
        "mapa_anegamientos_ifs_20260718_06utc_20260718-100049.html",
    ], carpeta="ifs")
    hallados = mapas_de_ciclo(_cfg(tmp_path), "gfs", "2026-07-18T06:00:00+00:00")
    assert [p.name for p in hallados] == [
        "mapa_anegamientos_gfs_20260718_06utc_20260718-100049.html"]


def test_mapas_de_ciclo_vacio_si_no_hay_nada(tmp_path, monkeypatch):
    _preparar(tmp_path, monkeypatch, [])
    assert mapas_de_ciclo(_cfg(tmp_path), "gfs", "2026-07-18T06:00:00+00:00") == []


def test_mapas_de_ciclo_vacio_para_escenario_sin_ciclo(tmp_path, monkeypatch):
    """Sin ciclo el patrón sería `mapa_anegamientos_gfs_*.html` y barrería todo."""
    _preparar(tmp_path, monkeypatch, [
        "mapa_anegamientos_gfs_20260718_06utc_20260718-100049.html"])
    assert mapas_de_ciclo(_cfg(tmp_path), "extremo_200mm", None) == []
