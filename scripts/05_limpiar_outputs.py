#!/usr/bin/env python
"""Poda outputs/: renders duplicados de un ciclo y, opcionalmente, ciclos viejos.

Simula por default; hay que pasar --aplicar para que borre. Ejemplos:

    python scripts/05_limpiar_outputs.py                      # qué sobra (no borra)
    python scripts/05_limpiar_outputs.py --aplicar            # borra duplicados
    python scripts/05_limpiar_outputs.py --conservar-ciclos 8 --aplicar
    python scripts/05_limpiar_outputs.py --config config_atacama.yaml --aplicar
"""

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from inundaciones.limpieza import limpiar
from inundaciones.utils import cargar_config

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--aplicar", action="store_true",
                        help="borra de verdad; sin esto solo informa")
    parser.add_argument("--conservar-ciclos", type=int, default=None, metavar="N",
                        help="además, deja solo los N ciclos más recientes de cada "
                             "fuente (esto SÍ descarta historial; por default no se hace)")
    parser.add_argument("--config", default=None,
                        help="config alternativo por región (p. ej. config_atacama.yaml)")
    args = parser.parse_args()

    # el handler lo instala inundaciones.utils al importarse; agregar
    # basicConfig acá duplicaría cada línea (el logger propaga a la raíz)
    limpiar(cargar_config(args.config),
            conservar_ciclos=args.conservar_ciclos, aplicar=args.aplicar)
