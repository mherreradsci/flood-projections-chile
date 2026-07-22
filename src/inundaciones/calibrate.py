"""Calibración del modelo contra huellas históricas (Global Flood Database).

Por subcuenca y evento:
 1. Se corre SCS-CN con la lluvia/isoterma observadas del evento (factor 1).
 2. h* = percentil 80 del HAND en las celdas observadas de la subcuenca: el
    corredor mínimo que captura la mayoría de la observación. (El CSI celda a
    celda contra MODIS 250 m es estructuralmente bajo — el sensor solo ve
    cuerpos de agua grandes — así que maximizarlo amplifica ruido de ladera.)
 3. factor = volumen(h*) / volumen_modelado. El factor absorbe los procesos
    no modelados (tránsito, duración, pérdidas) y los sesgos de la huella.
 4. Se reportan CSI/POD/FAR en h* como referencia.

Solo se calibra donde hay observación suficiente (n_obs ≥ MIN_OBS); el resto
hereda la mediana regional de los factores válidos — la ausencia de detección
MODIS no implica ausencia de inundación. Resultado: data/calibracion.json +
reporte CSV.
"""

import json
from pathlib import Path

import numpy as np
import pandas as pd

from .runoff import calcular_escorrentia, rasterizar_subcuencas
from .utils import area_celda_m2, cargar_config, leer_raster, log, ruta_data, ruta_outputs

MIN_OBS = 20  # celdas observadas mínimas para calibrar una subcuenca


def _metricas(modelo: np.ndarray, observado: np.ndarray) -> dict:
    tp = int((modelo & observado).sum())
    fp = int((modelo & ~observado).sum())
    fn = int((~modelo & observado).sum())
    return {
        "CSI": tp / (tp + fp + fn) if tp + fp + fn else np.nan,
        "POD": tp / (tp + fn) if tp + fn else np.nan,
        "FAR": fp / (tp + fp) if tp + fp else np.nan,
    }


def calibrar(cfg: dict) -> Path:
    hand, transform, _ = leer_raster(ruta_data(cfg, "dem", "hand.tif"))
    ids, _, _ = leer_raster(rasterizar_subcuencas(cfg))
    hand_max = float(cfg["terreno"]["hand_max_m"])
    lat_media = (cfg["region"]["bbox"][1] + cfg["region"]["bbox"][3]) / 2
    celda_m2 = area_celda_m2(transform, lat_media)
    f_min = float(cfg["calibracion"]["factor_min"])
    f_max = float(cfg["calibracion"]["factor_max"])

    filas = []
    for evento in cfg["calibracion"]["eventos"]:
        ruta_huella = ruta_data(cfg, "historical", f"huella_{evento['nombre']}.tif")
        if not ruta_huella.exists():
            log.warning("Sin huella para %s; evento omitido", evento["nombre"])
            continue
        huella = leer_raster(ruta_huella)[0] == 1
        vol = calcular_escorrentia(cfg, precip_mm=evento["precipitacion_mm"],
                                   isoterma_m=evento["isoterma0_m"])

        for fila in vol.itertuples():
            mascara = (ids == fila.id_raster) & (hand >= 0) & (hand < hand_max)
            n_obs = int((huella & mascara).sum())
            if not mascara.any() or fila.volumen_m3 <= 0:
                continue
            if n_obs < MIN_OBS:
                # observación insuficiente: heredará la mediana regional
                filas.append({"evento": evento["nombre"], "HYBAS_ID": fila.HYBAS_ID,
                              "n_obs": n_obs, "factor": np.nan,
                              "CSI": np.nan, "POD": np.nan, "FAR": np.nan})
                continue

            hand_sub = hand[mascara]
            obs_sub = huella[mascara]
            # corredor mínimo que captura el 80% de las celdas observadas
            h_obs = float(np.clip(np.percentile(hand_sub[obs_sub], 80), 0.25, hand_max))
            m = _metricas(hand_sub <= h_obs, obs_sub)

            valores = np.sort(hand_sub.astype("float64"))
            k = int(np.searchsorted(valores, h_obs, side="right"))
            v_obs = float((h_obs * k - valores[:k].sum()) * celda_m2)
            factor = float(np.clip(v_obs / fila.volumen_m3, f_min, f_max))
            filas.append({"evento": evento["nombre"], "HYBAS_ID": fila.HYBAS_ID,
                          "n_obs": n_obs, "h_obs_m": h_obs, "factor": factor, **m})

    if not filas:
        raise RuntimeError("No hay eventos con huella para calibrar")

    reporte = pd.DataFrame(filas)
    ruta_csv = ruta_outputs(cfg, "calibracion_reporte.csv")
    reporte.to_csv(ruta_csv, index=False)

    # factor final: media geométrica entre eventos con observación suficiente
    con_obs = reporte.dropna(subset=["factor"])
    factores = (con_obs.groupby("HYBAS_ID").factor
                .apply(lambda s: float(np.exp(np.log(s).mean()))).to_dict())
    factores = {str(k): float(np.clip(v, f_min, f_max)) for k, v in factores.items()}

    # subcuencas sin observación suficiente heredan la mediana regional
    mediana = float(np.median(list(factores.values()))) if factores else 1.0
    sin_obs = reporte[reporte.factor.isna()].HYBAS_ID.unique()
    for hybas in sin_obs:
        factores.setdefault(str(hybas), mediana)

    destino = ruta_data(cfg, "calibracion.json")
    destino.write_text(json.dumps(factores, indent=2))
    csi_medio = float(con_obs.CSI.mean()) if not con_obs.empty else float("nan")
    log.info("Calibración: %d subcuencas observadas (factor mediano %.2f, "
             "CSI medio %.2f) + %d con factor regional heredado. Reporte: %s",
             len(con_obs.HYBAS_ID.unique()), mediana, csi_medio, len(sin_obs),
             ruta_csv)
    return destino


def cargar_factores(cfg: dict) -> dict[str, float]:
    ruta = ruta_data(cfg, "calibracion.json")
    if ruta.exists():
        return json.loads(ruta.read_text())
    log.warning("Sin calibración previa; se usa factor por defecto")
    return {}


if __name__ == "__main__":
    cfg = cargar_config()
    print(calibrar(cfg))
