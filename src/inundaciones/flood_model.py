"""Conversión volumen de escorrentía → extensión/profundidad de anegamiento.

Por subcuenca se sube un umbral h en el espacio HAND hasta que el volumen
contenido bajo él (Σ (h − HAND_i)·área_celda) iguala la escorrentía del evento
(enfoque tipo FwDET/GeoFlood). Profundidad = h − HAND en las celdas anegadas.
"""


import numpy as np
import pandas as pd

from .runoff import rasterizar_subcuencas
from .utils import (
    area_celda_m2,
    cargar_config,
    guardar_raster,
    leer_raster,
    log,
    ruta_data,
    ruta_outputs,
)

PROFUNDIDAD_MIN_M = 0.05  # bajo esto se considera ruido, no anegamiento


def _umbral_para_volumen(hand_orden: np.ndarray, volumen_m3: float,
                         celda_m2: float, hand_max: float) -> float:
    """Umbral h (m) tal que el volumen HAND acumulado iguala volumen_m3.

    hand_orden: valores HAND válidos de la subcuenca, ordenados ascendentes.
    """
    if volumen_m3 <= 0 or hand_orden.size == 0:
        return 0.0
    prefijo = np.concatenate([[0.0], np.cumsum(hand_orden)])
    k = np.arange(len(hand_orden) + 1)
    # volumen si el umbral queda exactamente en el k-ésimo valor
    vol_k = (hand_orden[np.minimum(k, len(hand_orden) - 1)] * k - prefijo[:len(k)]) * celda_m2
    vol_k[0] = 0.0
    idx = int(np.searchsorted(vol_k, volumen_m3))
    if idx >= len(hand_orden):
        # el volumen excede la capacidad bajo hand_max: h = hand_max + excedente plano
        vol_max = (hand_max * len(hand_orden) - prefijo[-1]) * celda_m2
        if volumen_m3 >= vol_max:
            return hand_max
        extra = (volumen_m3 - vol_k[-1]) / (len(hand_orden) * celda_m2)
        return float(min(hand_orden[-1] + extra, hand_max))
    # interpolación dentro del tramo [idx-1, idx]
    n = max(idx, 1)
    h = hand_orden[idx - 1] + (volumen_m3 - vol_k[idx - 1]) / (n * celda_m2)
    return float(min(max(h, 0.0), hand_max))


def modelar_inundacion(cfg: dict, volumenes: pd.DataFrame,
                       sufijo: str = "proyectada") -> dict:
    """Raster de profundidad (m) y máscara de extensión para los volúmenes dados."""
    hand, transform, _ = leer_raster(ruta_data(cfg, "dem", "hand.tif"))
    ids, _, _ = leer_raster(rasterizar_subcuencas(cfg))
    hand_max = float(cfg["terreno"]["hand_max_m"])
    lat_media = (cfg["region"]["bbox"][1] + cfg["region"]["bbox"][3]) / 2
    celda_m2 = area_celda_m2(transform, lat_media)

    profundidad = np.zeros(hand.shape, dtype="float32")
    umbrales = {}
    for fila in volumenes.itertuples():
        mascara = (ids == fila.id_raster) & (hand >= 0) & (hand < hand_max)
        if not mascara.any():
            continue
        valores = np.sort(hand[mascara].astype("float64"))
        h = _umbral_para_volumen(valores, float(fila.volumen_m3), celda_m2, hand_max)
        umbrales[str(fila.HYBAS_ID)] = h
        if h > 0:
            d = h - hand[mascara]
            celdas = np.zeros(hand.shape, dtype="float32")
            celdas[mascara] = np.maximum(d, 0)
            profundidad = np.maximum(profundidad, celdas)

    extension = (profundidad >= PROFUNDIDAD_MIN_M).astype("uint8")
    ruta_prof = ruta_outputs(cfg, f"profundidad_{sufijo}.tif")
    ruta_ext = ruta_outputs(cfg, f"extension_{sufijo}.tif")
    guardar_raster(ruta_prof, profundidad, transform, nodata=-9999)
    guardar_raster(ruta_ext, extension, transform, nodata=255, dtype="uint8")

    area_km2 = extension.sum() * celda_m2 / 1e6
    log.info("Inundación '%s': %.1f km² anegados, profundidad máx %.1f m",
             sufijo, area_km2, float(profundidad.max()))
    return {"profundidad": ruta_prof, "extension": ruta_ext,
            "umbrales": umbrales, "area_km2": float(area_km2)}


if __name__ == "__main__":
    from .runoff import calcular_escorrentia
    cfg = cargar_config()
    df = calcular_escorrentia(cfg)
    print(modelar_inundacion(cfg, df))
