"""Utilidades compartidas: configuración, rutas, logging y rasters."""

import logging
from pathlib import Path

import numpy as np
import rasterio
import yaml
from rasterio.transform import Affine

RAIZ = Path(__file__).resolve().parents[2]

log = logging.getLogger("inundaciones")
if not log.handlers:
    _h = logging.StreamHandler()
    _h.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(message)s", "%H:%M:%S"))
    log.addHandler(_h)
    log.setLevel(logging.INFO)


def cargar_config(ruta: str | Path | None = None) -> dict:
    ruta = Path(ruta) if ruta else RAIZ / "config.yaml"
    with open(ruta, encoding="utf-8") as f:
        return yaml.safe_load(f)


def _ruta_base(cfg: dict, clave: str, *partes: str) -> Path:
    """Ruta bajo data/ u outputs/; con region.id definido agrega una
    subcarpeta por región (multi-región). Sin id se mantiene el layout
    plano histórico."""
    p = RAIZ / cfg["rutas"][clave]
    region_id = cfg.get("region", {}).get("id")
    if region_id:
        p = p / region_id
    for parte in partes:
        p = p / parte
    p.parent.mkdir(parents=True, exist_ok=True)
    return p


def ruta_data(cfg: dict, *partes: str) -> Path:
    return _ruta_base(cfg, "data", *partes)


def ruta_outputs(cfg: dict, *partes: str) -> Path:
    return _ruta_base(cfg, "outputs", *partes)


def guardar_raster(ruta: Path, datos: np.ndarray, transform: Affine, crs="EPSG:4326",
                   nodata=None, dtype=None) -> Path:
    dtype = dtype or datos.dtype
    perfil = {
        "driver": "GTiff", "height": datos.shape[0], "width": datos.shape[1],
        "count": 1, "dtype": dtype, "crs": crs, "transform": transform,
        "compress": "deflate", "tiled": True,
    }
    if nodata is not None:
        perfil["nodata"] = nodata
    ruta.parent.mkdir(parents=True, exist_ok=True)
    with rasterio.open(ruta, "w", **perfil) as dst:
        dst.write(datos.astype(dtype), 1)
    return ruta


def leer_raster(ruta: Path) -> tuple[np.ndarray, Affine, object]:
    """Devuelve (banda 1, transform, crs)."""
    with rasterio.open(ruta) as src:
        return src.read(1), src.transform, src.crs


def area_celda_m2(transform: Affine, lat_media: float) -> float:
    """Área aproximada de una celda geográfica (grados) en m²."""
    dx = abs(transform.a) * 111_320 * np.cos(np.radians(lat_media))
    dy = abs(transform.e) * 110_540
    return dx * dy
