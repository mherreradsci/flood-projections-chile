#!/usr/bin/env python
"""Muestra qué ciclos GFS ya están completos en los servidores de NOAA.

Un ciclo se publica de forma progresiva (~1.5 h); "vigente" para el pipeline
es el más reciente que ya publicó hasta el horizonte de config.yaml
(pronostico.horas, hoy 72 h). Solo consulta los .idx con HEAD, no descarga.

Uso:
  python scripts/ciclo_vigente.py
  python scripts/ciclo_vigente.py --horas 48 --max-ciclos 6
"""

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from inundaciones.ingest_forecast import sondear_ciclos_gfs
from inundaciones.utils import cargar_config


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--horas", type=int, default=None,
                        help="horizonte requerido (defecto: pronostico.horas del config)")
    parser.add_argument("--max-ciclos", type=int, default=4,
                        help="cuántos ciclos hacia atrás sondear (defecto: 4)")
    args = parser.parse_args()

    horas = args.horas or int(cargar_config()["pronostico"]["horas"])
    print(f"Ciclos GFS en NOAA (noaa-gfs-bdp-pds, horizonte requerido: {horas} h)\n")

    vigente = None
    for r in sondear_ciclos_gfs(horas=horas, max_ciclos=args.max_ciclos):
        c = r["ciclo"]
        if r["completo"]:
            estado = "completo ✓"
            vigente = vigente or c
        elif r["ultima_fxx"] is None:
            estado = "sin publicar aún"
        else:
            estado = f"incompleto — publicado hasta f{r['ultima_fxx']:03d}"
        print(f"  {c:%Y-%m-%d %H} UTC  {estado}")

    if vigente:
        print(f"\nCiclo vigente para el pipeline: {vigente:%Y-%m-%d %H} UTC"
              f"  (tag de mapa: _{vigente:%Y%m%d}_{vigente:%H}utc)")
    else:
        print("\nNingún ciclo sondeado tiene el horizonte completo; "
              "aumenta --max-ciclos o revisa la conexión.")


if __name__ == "__main__":
    main()
