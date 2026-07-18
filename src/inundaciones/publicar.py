"""Copia estable de resultados para el servicio de publicación externo.

Cada corrida de 04_proyectar deja su mapa con nombre fijo en
publicacion/<region>/mapa_<sufijo>.html (se sobrescribe: siempre la última
corrida de esa fuente) y actualiza dos manifiestos:

  publicacion/<region>/manifest.json — nombre de la región e ítems por sufijo
  publicacion/manifest.json          — índice global: todas las regiones con
                                       sus ítems (rutas relativas a publicacion/)

El servicio externo (carrusel por región) solo necesita leer el manifiesto
global; los nombres timestampeados de outputs/ quedan como historial.
"""

import json
import shutil
from datetime import datetime
from pathlib import Path

from . import ingest_forecast
from .utils import RAIZ, log


def _dir_publicacion(cfg: dict) -> Path:
    return RAIZ / cfg["rutas"].get("publicacion", "publicacion")


def publicar_mapa(cfg: dict, ruta_mapa: Path, sufijo: str) -> Path:
    """Copia el mapa con nombre estable y actualiza ambos manifiestos."""
    region_id = cfg["region"].get("id") or "region"
    dir_region = _dir_publicacion(cfg) / region_id
    dir_region.mkdir(parents=True, exist_ok=True)
    destino = dir_region / f"mapa_{sufijo}.html"
    shutil.copy2(ruta_mapa, destino)

    item = {"sufijo": sufijo, "archivo": destino.name,
            "publicado": datetime.now().isoformat(timespec="seconds")}
    ruta_meta = ingest_forecast.ruta_meta(cfg, sufijo)
    if ruta_meta.exists():
        # fuente, ciclo, horas, isoterma 0 y acumulados de la corrida
        item.update(json.loads(ruta_meta.read_text()))

    ruta_manifest = dir_region / "manifest.json"
    manifest = {"id": region_id, "nombre": cfg["region"]["nombre"], "items": {}}
    if ruta_manifest.exists():
        manifest = json.loads(ruta_manifest.read_text())
        manifest["nombre"] = cfg["region"]["nombre"]
    manifest["items"][sufijo] = item
    ruta_manifest.write_text(json.dumps(manifest, ensure_ascii=False, indent=2))

    _reconstruir_indice(cfg)
    log.info("Publicado: %s", destino)
    return destino


def _reconstruir_indice(cfg: dict) -> Path:
    """Índice global agregando los manifiestos de todas las regiones."""
    base = _dir_publicacion(cfg)
    regiones = []
    for m in sorted(base.glob("*/manifest.json")):
        region = json.loads(m.read_text())
        region["items"] = [dict(v, archivo=f"{m.parent.name}/{v['archivo']}")
                           for v in region["items"].values()]
        regiones.append(region)
    indice = {"actualizado": datetime.now().isoformat(timespec="seconds"),
              "regiones": regiones}
    destino = base / "manifest.json"
    destino.write_text(json.dumps(indice, ensure_ascii=False, indent=2))
    return destino
