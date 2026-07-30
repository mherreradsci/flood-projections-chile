"""Poda de outputs/: renders duplicados de un ciclo y ciclos antiguos.

`outputs/` es el historial timestampeado y crece sin techo: cada corrida de
`04_proyectar.py` agrega un HTML de ~4 MB y su raster `extension_*` pareado
(ver `mapa.generar_mapa`), y reprocesar un ciclo agrega otro par para el
*mismo* ciclo en vez de reemplazarlo (el nombre lleva ciclo y timestamp de
render). `reprocesar_ciclo_gfs.py` evita crear ese duplicado avisando, pero
la ruta del cron no, así que igual se acumulan.

Dos podas, independientes entre sí:

- `duplicados_por_ciclo`: de cada ciclo deja solo el render más nuevo. Nunca
  pierde información — los renders viejos del mismo ciclo son intentos
  superados del mismo pronóstico.
- `ciclos_excedentes`: deja los N ciclos más recientes. Esta sí descarta
  historial, por eso es opt-in y no corre por default.

Escenarios sintéticos y el formato legado sin fecha (`..._ifs_12utc_...`) no
matchean el patrón y quedan siempre intactos.
"""

import re
from collections import defaultdict
from pathlib import Path

from .utils import log, ruta_outputs

# Espejo del nombre que arma `mapa.generar_mapa` con `utils.ciclo_tag`:
#   mapa_anegamientos_<sufijo><_YYYYMMDD_HHutc>_<YYYYMMDD-HHMMSS>.html
# Es la dirección inversa de ciclo_tag, así que puede desincronizarse de él;
# tests/test_limpieza.py compone ciclo_tag() con este patrón para impedirlo.
PATRON_MAPA = re.compile(
    r"^mapa_anegamientos_(?P<sufijo>.+?)_(?P<ciclo>\d{8}_\d{2}utc)_"
    r"(?P<render>\d{8}-\d{6})\.html$"
)


def _raster_de_mapa(ruta_html: Path) -> Path:
    """Ruta del raster de susceptibilidad que acompaña un HTML de mapa:
    mismo nombre base, con "_extension" insertado después del sufijo y
    ".tif" en vez de ".html" (ver `mapa.generar_mapa`, que arma el mismo
    nombre). No valida que `ruta_html` matchee `PATRON_MAPA` — llamar solo
    sobre matches."""
    m = PATRON_MAPA.match(ruta_html.name)
    return ruta_html.with_name(
        f"mapa_anegamientos_{m['sufijo']}_extension_{m['ciclo']}_{m['render']}.tif")


def _con_raster(htmls: list[Path]) -> list[Path]:
    """Cada HTML de mapa junto con su raster pareado, si existe."""
    salida = []
    for f in htmls:
        salida.append(f)
        raster = _raster_de_mapa(f)
        if raster.exists():
            salida.append(raster)
    return salida


def _por_ciclo(raiz: Path) -> dict[tuple[str, str], list[tuple[str, Path]]]:
    """Agrupa los mapas de `raiz` en {(sufijo, ciclo): [(render, ruta), ...]}.

    `rglob` en vez de `glob`: los mapas viven en `raiz/<gfs|ifs|escenario>/`
    (ver `utils.ruta_salida`), no sueltos en `raiz`.

    Los valores quedan ordenados por timestamp de render, que es `%Y%m%d-%H%M%S`
    y por lo tanto ordena lexicográficamente igual que cronológicamente.
    """
    grupos: dict[tuple[str, str], list[tuple[str, Path]]] = defaultdict(list)
    for f in raiz.rglob("*.html"):
        m = PATRON_MAPA.match(f.name)
        if m:
            grupos[(m["sufijo"], m["ciclo"])].append((m["render"], f))
    return {k: sorted(v) for k, v in grupos.items()}


def duplicados_por_ciclo(raiz: Path) -> list[Path]:
    """Renders sobrantes: de cada ciclo, todos menos el más reciente (HTML y
    su raster `extension_*` pareado, si existe)."""
    htmls = [f for versiones in _por_ciclo(raiz).values() for _, f in versiones[:-1]]
    return sorted(_con_raster(htmls))


def ciclos_excedentes(raiz: Path, conservar: int) -> list[Path]:
    """Todos los archivos de los ciclos más viejos, dejando los `conservar`
    ciclos más recientes de cada fuente.

    Se cuenta por ciclo y no por archivo: un ciclo con tres renders sigue
    siendo un ciclo, así que `conservar=8` deja ocho pronósticos distintos
    aunque alguno se haya renderizado varias veces.
    """
    if conservar <= 0:
        return []
    grupos = _por_ciclo(raiz)
    por_fuente: dict[str, list[str]] = defaultdict(list)
    for sufijo, ciclo in grupos:
        por_fuente[sufijo].append(ciclo)
    sobran = []
    for sufijo, ciclos in por_fuente.items():
        # el ciclo es YYYYMMDD_HHutc: orden lexicográfico = cronológico
        for ciclo in sorted(ciclos)[:-conservar]:
            sobran += [f for _, f in grupos[(sufijo, ciclo)]]
    return sorted(_con_raster(sobran))


def limpiar(cfg: dict, conservar_ciclos: int | None = None,
            aplicar: bool = False) -> list[Path]:
    """Poda outputs/ de la región y devuelve los archivos afectados.

    Sin `aplicar` no borra nada: solo informa qué se borraría. Es el default a
    propósito — `outputs/` está en .gitignore y no hay forma de recuperar.
    """
    raiz = ruta_outputs(cfg)
    sobran = duplicados_por_ciclo(raiz)
    if conservar_ciclos:
        sobran = sorted(set(sobran) | set(ciclos_excedentes(raiz, conservar_ciclos)))
    mb = sum(f.stat().st_size for f in sobran) / 1024 / 1024
    for f in sobran:
        log.info("%s %s", "borrando" if aplicar else "sobra", f.name)
        if aplicar:
            f.unlink()
    log.info("%s: %d archivo(s), %.1f MB%s", raiz, len(sobran), mb,
             "" if aplicar else " (simulación; usar --aplicar para borrar)")
    return sobran
