#!/usr/bin/env python
"""Descarga todos los insumos: AOI, DEM, land cover, huellas históricas y pronóstico.

Uso:
  python scripts/01_descargar_datos.py                 # pronóstico GFS vigente
  python scripts/01_descargar_datos.py --sin-pronostico
"""

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from inundaciones import aoi, ingest_dem, ingest_forecast, ingest_historical, ingest_landcover
from inundaciones.utils import cargar_config, log


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--sin-pronostico", action="store_true",
                        help="omite la descarga GFS (útil si se usará un escenario)")
    parser.add_argument("--config", default=None,
                        help="config alternativo por región (p. ej. config_atacama.yaml)")
    args = parser.parse_args()

    cfg = cargar_config(args.config)
    aoi.obtener_region(cfg)
    aoi.obtener_subcuencas(cfg)
    ingest_dem.preparar_dem(cfg)
    ingest_landcover.preparar_curve_number(cfg)
    ingest_historical.preparar_huellas(cfg)
    if not args.sin_pronostico:
        ingest_forecast.descargar_gfs(cfg)
    log.info("Descarga de insumos completa")


if __name__ == "__main__":
    main()
