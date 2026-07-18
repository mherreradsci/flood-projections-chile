#!/usr/bin/env python
"""Calibra el modelo contra las huellas históricas y guarda data/calibracion.json."""

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from inundaciones.calibrate import calibrar
from inundaciones.utils import cargar_config

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", default=None,
                        help="config alternativo por región (p. ej. config_atacama.yaml)")
    args = parser.parse_args()
    print(calibrar(cargar_config(args.config)))
