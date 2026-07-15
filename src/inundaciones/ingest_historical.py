"""Huellas de inundación históricas: Global Flood Database v1.4 (bucket GCS público).

Los archivos se llaman DFO_<id>_From_<YYYYMMDD>_to_<YYYYMMDD>.tif (MODIS 250 m,
banda 1 = celdas inundadas 0/1). Se buscan por rango de fechas de los eventos de
calibración definidos en config.yaml y se reproyectan a la grilla del DEM.
"""

import re
from datetime import datetime
from pathlib import Path

import numpy as np
import rasterio
import requests
from rasterio.warp import Resampling, reproject

from .utils import cargar_config, guardar_raster, leer_raster, log, ruta_data

API_LISTADO = "https://storage.googleapis.com/storage/v1/b/gfd_v1_4/o"
URL_OBJETO = "https://storage.googleapis.com/gfd_v1_4/{nombre}"
PATRON = re.compile(r"DFO_(\d+)_From_(\d{8})_to_(\d{8})\.(tif|zip)$")


def _ruta_rasterio(ruta: Path) -> str:
    """Ruta abrible por rasterio; para zips apunta al primer .tif interno."""
    if ruta.suffix != ".zip":
        return str(ruta)
    import zipfile
    with zipfile.ZipFile(ruta) as zf:
        tifs = [m for m in zf.namelist() if m.lower().endswith(".tif")]
    if not tifs:
        raise RuntimeError(f"Zip GFD sin GeoTIFF: {ruta.name}")
    return f"zip://{ruta}!{tifs[0]}"


def listar_eventos_gfd() -> list[dict]:
    """Lista completa de eventos del bucket (nombre, id, fechas)."""
    eventos, token = [], None
    while True:
        params = {"fields": "items(name),nextPageToken", "maxResults": 1000}
        if token:
            params["pageToken"] = token
        r = requests.get(API_LISTADO, params=params, timeout=120)
        r.raise_for_status()
        js = r.json()
        for item in js.get("items", []):
            m = PATRON.search(item["name"])
            if m:
                eventos.append({
                    "nombre": item["name"],
                    "dfo_id": int(m.group(1)),
                    "inicio": datetime.strptime(m.group(2), "%Y%m%d"),
                    "fin": datetime.strptime(m.group(3), "%Y%m%d"),
                })
        token = js.get("nextPageToken")
        if not token:
            return eventos


def _intersecta_bbox(ruta_tif: Path, bbox) -> bool:
    o, s, e, n = bbox
    with rasterio.open(_ruta_rasterio(ruta_tif)) as src:
        b = src.bounds
    return not (b.right < o or b.left > e or b.top < s or b.bottom > n)


def descargar_huella_evento(cfg: dict, evento_cfg: dict,
                            catalogo: list[dict]) -> Path | None:
    """Descarga la huella GFD del evento y la lleva a la grilla del DEM (0/1)."""
    destino = ruta_data(cfg, "historical", f"huella_{evento_cfg['nombre']}.tif")
    if destino.exists():
        return destino

    ini = datetime.fromisoformat(evento_cfg["inicio"])
    fin = datetime.fromisoformat(evento_cfg["fin"])
    candidatos = [ev for ev in catalogo if ev["inicio"] <= fin and ev["fin"] >= ini]
    if not candidatos:
        log.warning("Sin evento GFD entre %s y %s", evento_cfg["inicio"], evento_cfg["fin"])
        return None

    bbox = cfg["region"]["bbox"]
    for ev in candidatos:
        bruto = ruta_data(cfg, "historical", ev["nombre"])
        if not bruto.exists():
            log.info("Descargando huella GFD %s", ev["nombre"])
            r = requests.get(URL_OBJETO.format(nombre=ev["nombre"]), timeout=600)
            r.raise_for_status()
            bruto.write_bytes(r.content)
        if not _intersecta_bbox(bruto, bbox):
            log.info("  %s no intersecta la región, descartado", ev["nombre"])
            continue

        dem, transform_dem, crs_dem = leer_raster(ruta_data(cfg, "dem", "dem.tif"))
        with rasterio.open(_ruta_rasterio(bruto)) as src:
            huella = np.zeros(dem.shape, dtype="uint8")
            reproject(
                rasterio.band(src, 1), huella,
                dst_transform=transform_dem, dst_crs=crs_dem,
                resampling=Resampling.nearest,
            )
        # depurar falsos positivos MODIS: océano/playa y agua permanente
        huella[dem < 1.0] = 0
        ruta_lc = ruta_data(cfg, "landcover", "worldcover.tif")
        if ruta_lc.exists():
            lc = leer_raster(ruta_lc)[0]
            huella[lc == 80] = 0
        if huella.sum() == 0:
            log.info("  %s intersecta pero sin celdas inundadas en la región", ev["nombre"])
            continue
        guardar_raster(destino, huella, transform_dem, nodata=255, dtype="uint8")
        log.info("Huella %s: %d celdas inundadas (DFO %d)",
                 evento_cfg["nombre"], int(huella.sum()), ev["dfo_id"])
        return destino

    log.warning("Ningún candidato GFD útil para %s", evento_cfg["nombre"])
    return None


def construir_union(cfg: dict) -> Path | None:
    """Une todas las huellas de evento existentes (GFD y Sentinel-1)."""
    huellas = [p for p in ruta_data(cfg, "historical", "x").parent.glob("huella_*.tif")
               if p.name != "huella_historica_union.tif"]
    if not huellas:
        return None
    dem, transform_dem, _ = leer_raster(ruta_data(cfg, "dem", "dem.tif"))
    union = np.zeros(dem.shape, dtype="uint8")
    for ruta in huellas:
        union |= (leer_raster(ruta)[0] == 1).astype("uint8")
    destino = ruta_data(cfg, "historical", "huella_historica_union.tif")
    guardar_raster(destino, union, transform_dem, nodata=255, dtype="uint8")
    log.info("Unión histórica (%d eventos): %d celdas", len(huellas), int(union.sum()))
    return destino


def preparar_huellas(cfg: dict) -> dict[str, Path]:
    """Descarga las huellas GFD de los eventos de calibración y rehace la unión."""
    eventos_gfd = [e for e in cfg["calibracion"]["eventos"]
                   if e.get("fuente", "gfd") == "gfd"]
    rutas = {}
    pendientes = [e for e in eventos_gfd
                  if not ruta_data(cfg, "historical",
                                   f"huella_{e['nombre']}.tif").exists()]
    catalogo = listar_eventos_gfd() if pendientes else []
    if catalogo:
        log.info("Catálogo GFD: %d eventos globales", len(catalogo))
    for ev in eventos_gfd:
        destino = ruta_data(cfg, "historical", f"huella_{ev['nombre']}.tif")
        ruta = destino if destino.exists() else \
            descargar_huella_evento(cfg, ev, catalogo)
        if ruta:
            rutas[ev["nombre"]] = ruta

    union = construir_union(cfg)
    if union:
        rutas["union"] = union
    return rutas


if __name__ == "__main__":
    cfg = cargar_config()
    print(preparar_huellas(cfg))
