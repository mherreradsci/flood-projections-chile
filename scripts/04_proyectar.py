#!/usr/bin/env python
"""Proyecta anegamientos con la lluvia elegida y genera el mapa final.

Uso:
  python scripts/04_proyectar.py --fuente gfs
  python scripts/04_proyectar.py --fuente escenario --escenario extremo_200mm
  python scripts/04_proyectar.py --fuente escenario --escenario extremo_200mm --sin-exposicion
"""

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from inundaciones import ingest_forecast
from inundaciones.calibrate import cargar_factores
from inundaciones.exposure import evaluar_exposicion
from inundaciones.flood_model import modelar_inundacion
from inundaciones.mapa import generar_mapa
from inundaciones.new_areas import identificar_zonas_nuevas
from inundaciones.runoff import calcular_escorrentia
from inundaciones.utils import cargar_config, log


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--fuente", choices=["gfs", "ifs", "escenario"], default="gfs")
    parser.add_argument("--escenario", default="extremo_200mm",
                        help="nombre del escenario en config.yaml")
    parser.add_argument("--sin-exposicion", action="store_true",
                        help="omite la consulta OSM de infraestructura expuesta")
    args = parser.parse_args()

    cfg = cargar_config()
    if args.fuente in ("gfs", "ifs"):
        ingest_forecast.descargar_pronostico(cfg, modelo=args.fuente)
        sufijo = args.fuente
    else:
        ingest_forecast.generar_escenario(cfg, args.escenario)
        sufijo = args.escenario

    factores = cargar_factores(cfg)
    volumenes = calcular_escorrentia(cfg, factores=factores)
    resultado = modelar_inundacion(cfg, volumenes, sufijo=sufijo)
    identificar_zonas_nuevas(cfg, sufijo=sufijo)
    if not args.sin_exposicion:
        try:
            evaluar_exposicion(cfg, sufijo=sufijo)
        except Exception as exc:
            log.warning("Exposición OSM falló (%s); el mapa se genera sin ella", exc)
    mapa = generar_mapa(cfg, sufijo=sufijo)
    log.info("Listo. Extensión proyectada: %.1f km². Abrir: %s",
             resultado["area_km2"], mapa)


if __name__ == "__main__":
    main()
