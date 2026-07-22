"""Escorrentía por subcuenca: método SCS Curve Number con filtro de isoterma 0.

Solo el área de cada subcuenca bajo (isoterma 0 − banda de nieve húmeda) recibe
lluvia líquida y genera escorrentía. En eventos de río atmosférico cálido la
isoterma alta amplía dramáticamente esa área — el mecanismo clave de las
crecidas chilenas tipo 2015/2023.
"""

import json
from pathlib import Path

import numpy as np
import pandas as pd
from rasterio import features

from . import ingest_forecast
from .aoi import obtener_subcuencas
from .utils import area_celda_m2, cargar_config, guardar_raster, leer_raster, log, ruta_data


def rasterizar_subcuencas(cfg: dict) -> Path:
    """Raster de ids de subcuenca (1..N) en la grilla del DEM; 0 = fuera."""
    destino = ruta_data(cfg, "vector", "subcuencas_id.tif")
    if destino.exists():
        return destino
    sub = obtener_subcuencas(cfg)
    dem, transform, _ = leer_raster(ruta_data(cfg, "dem", "dem.tif"))
    formas = [(geom, i + 1) for i, geom in enumerate(sub.geometry)]
    ids = features.rasterize(formas, out_shape=dem.shape, transform=transform,
                             fill=0, dtype="int32")
    guardar_raster(destino, ids, transform, nodata=0, dtype="int32")
    return destino


def calcular_escorrentia(cfg: dict, factores: dict[str, float] | None = None,
                         precip_mm: float | None = None,
                         isoterma_m: float | None = None,
                         sufijo: str | None = None) -> pd.DataFrame:
    """Volumen de escorrentía (m³) por subcuenca.

    Por defecto usa la lluvia de la fuente `sufijo` (gfs, ifs o escenario;
    sin sufijo, el modelo de pronostico.modelo). `precip_mm` e `isoterma_m`
    permiten forzar un evento (calibración, sensibilidad) sin leer
    data/forecast. `factores`: factor de volumen calibrado por HYBAS_ID
    (str); sin calibrar se usa modelo.factor_volumen_defecto.
    """
    sub = obtener_subcuencas(cfg)
    dem, transform, _ = leer_raster(ruta_data(cfg, "dem", "dem.tif"))
    cn, _, _ = leer_raster(ruta_data(cfg, "landcover", "curve_number.tif"))
    ids, _, _ = leer_raster(rasterizar_subcuencas(cfg))
    sufijo = sufijo or cfg["pronostico"]["modelo"]
    if precip_mm is None:
        precip, _, _ = leer_raster(ingest_forecast.ruta_precip(cfg, sufijo))
    else:
        precip = np.full(dem.shape, float(precip_mm), dtype="float32")
    if isoterma_m is None:
        meta = json.loads(ingest_forecast.ruta_meta(cfg, sufijo).read_text())
        isoterma_m = meta["isoterma0_m"]

    lat_media = (cfg["region"]["bbox"][1] + cfg["region"]["bbox"][3]) / 2
    celda_m2 = area_celda_m2(transform, lat_media)
    cota_lluvia = isoterma_m - cfg["modelo"]["banda_nieve_humeda_m"]
    factor_defecto = float(cfg["modelo"]["factor_volumen_defecto"])
    factores = factores or {}

    filas = []
    for i, fila in enumerate(sub.itertuples()):
        mascara = ids == (i + 1)
        n_celdas = int(mascara.sum())
        if n_celdas == 0:
            continue
        pluvial = mascara & (dem > -9000) & (dem < cota_lluvia)
        n_pluvial = int(pluvial.sum())
        if n_pluvial == 0:
            filas.append({"HYBAS_ID": fila.HYBAS_ID, "id_raster": i + 1,
                          "area_km2": n_celdas * celda_m2 / 1e6, "frac_pluvial": 0.0,
                          "P_mm": 0.0, "CN": np.nan, "Q_mm": 0.0, "volumen_m3": 0.0})
            continue

        P = float(np.nanmean(precip[pluvial]))
        CN = float(np.nanmean(cn[pluvial]))
        S = 25400.0 / CN - 254.0          # retención potencial (mm)
        Ia = 0.2 * S                       # abstracción inicial
        Q = (P - Ia) ** 2 / (P + 0.8 * S) if P > Ia else 0.0
        factor = float(factores.get(str(fila.HYBAS_ID), factor_defecto))
        volumen = Q / 1000.0 * n_pluvial * celda_m2 * factor

        filas.append({"HYBAS_ID": fila.HYBAS_ID, "id_raster": i + 1,
                      "area_km2": n_celdas * celda_m2 / 1e6,
                      "frac_pluvial": n_pluvial / n_celdas,
                      "P_mm": P, "CN": CN, "Q_mm": Q, "volumen_m3": volumen})

    df = pd.DataFrame(filas)
    log.info("Escorrentía: %d subcuencas, volumen total %.1f hm³ "
             "(isoterma %d m → %.0f%% del área es pluvial)",
             len(df), df.volumen_m3.sum() / 1e6, isoterma_m,
             100 * np.average(df.frac_pluvial, weights=df.area_km2))
    return df


if __name__ == "__main__":
    cfg = cargar_config()
    df = calcular_escorrentia(cfg)
    print(df.to_string(index=False, float_format=lambda x: f"{x:.2f}"))
