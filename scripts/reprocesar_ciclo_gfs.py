#!/usr/bin/env python
"""Reprocesa ciclos GFS pasados puntuales (backfill) sin pisar el mapa publicado.

Uso:
  python scripts/reprocesar_ciclo_gfs.py --ciclo 2026-07-23T00
  python scripts/reprocesar_ciclo_gfs.py --ciclo 2026-07-23T00 --ciclo 2026-07-23T12
  python scripts/reprocesar_ciclo_gfs.py --ciclo 2026-07-23T06 --config config_atacama.yaml
  python scripts/reprocesar_ciclo_gfs.py --ciclo 2026-07-23T18 --publicar

Para cuándo sirve: el cron se cayó (o hubo que reinstalarlo) y quedaron
huecos en outputs/<region>/ para ciclos que sí llegaron a publicarse en
NOAA mientras tanto. `04_proyectar.py --fuente gfs` solo sabe pedir "el
ciclo más reciente"; este script fuerza un ciclo puntual monkeypencheando
`ingest_forecast._ciclo_gfs_para_descarga` (Herbie sí acepta cualquier
fecha, la resolución "más reciente" es solo nuestro default).

Por defecto NO publica: no toca `publicacion/<region>/mapa_gfs.html`, el
mapa vivo que consume el carrusel externo, porque se asume que se está
rellenando historia y no reemplazando el ciclo vigente. Para el ciclo que
sí es el vigente real, pasar --publicar (y volver a correr
`04_proyectar.py --fuente gfs` después si además se reprocesaron ciclos
más viejos, para que el mapa publicado quede en el ciclo correcto).
"""

import argparse
import sys
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from inundaciones import ingest_forecast
from inundaciones.calibrate import cargar_factores
from inundaciones.exposure import evaluar_exposicion
from inundaciones.flood_model import modelar_inundacion
from inundaciones.mapa import generar_mapa
from inundaciones.new_areas import identificar_zonas_nuevas
from inundaciones.publicar import publicar_mapa
from inundaciones.runoff import calcular_escorrentia
from inundaciones.utils import cargar_config, log


def _parse_ciclo(texto: str) -> datetime:
    dt = datetime.fromisoformat(texto)
    return dt.replace(tzinfo=timezone.utc) if dt.tzinfo is None else dt.astimezone(timezone.utc)


def reprocesar(cfg: dict, ciclo: datetime, *, publicar: bool, sin_exposicion: bool) -> Path:
    """Descarga y modela un ciclo GFS puntual (no necesariamente el más reciente)."""
    original = ingest_forecast._ciclo_gfs_para_descarga
    ingest_forecast._ciclo_gfs_para_descarga = lambda horas, c=ciclo: c
    try:
        _, meta = ingest_forecast.descargar_gfs(cfg)
    finally:
        ingest_forecast._ciclo_gfs_para_descarga = original

    if meta["ciclo"] != ciclo.isoformat():
        raise RuntimeError(
            f"Pedí el ciclo {ciclo.isoformat()} pero la descarga cayó en "
            f"{meta['ciclo']} (ciclo incompleto en NOAA o ya fuera de su "
            "ventana de retención); no reproceso con un ciclo distinto al pedido."
        )

    factores = cargar_factores(cfg)
    volumenes = calcular_escorrentia(cfg, factores=factores, sufijo="gfs")
    resultado = modelar_inundacion(cfg, volumenes, sufijo="gfs")
    identificar_zonas_nuevas(cfg, sufijo="gfs")
    if not sin_exposicion:
        try:
            evaluar_exposicion(cfg, sufijo="gfs")
        except Exception as exc:
            log.warning("Exposición OSM falló (%s); el mapa se genera sin ella", exc)
    mapa = generar_mapa(cfg, sufijo="gfs")
    if publicar:
        publicar_mapa(cfg, mapa, "gfs")
    log.info("Ciclo %s -> %.1f km² | %s", ciclo.isoformat(), resultado["area_km2"], mapa)
    return mapa


def main():
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--ciclo", action="append", required=True,
                        help="ciclo GFS a reprocesar, ISO UTC (p. ej. 2026-07-23T06); repetible")
    parser.add_argument("--config", default=None,
                        help="config alternativo por región (p. ej. config_atacama.yaml)")
    parser.add_argument("--publicar", action="store_true",
                        help="además actualiza publicacion/<region>/mapa_gfs.html (por defecto no)")
    parser.add_argument("--sin-exposicion", action="store_true",
                        help="omite la consulta OSM de infraestructura expuesta")
    args = parser.parse_args()

    cfg = cargar_config(args.config)
    for texto in args.ciclo:
        reprocesar(cfg, _parse_ciclo(texto), publicar=args.publicar,
                  sin_exposicion=args.sin_exposicion)


if __name__ == "__main__":
    main()
