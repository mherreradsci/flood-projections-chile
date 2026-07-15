"""Pronóstico de precipitación e isoterma 0: GFS 0.25° vía Herbie, o escenario sintético.

Salidas (siempre en la grilla del DEM):
  data/forecast/precip_mm.tif   — precipitación acumulada del evento (mm)
  data/forecast/meta.json       — fuente, ciclo, horizonte, isoterma 0 (m)
"""

import json
from datetime import datetime, timedelta, timezone
from pathlib import Path

import numpy as np
import rasterio
from rasterio.warp import Resampling, reproject

from .utils import cargar_config, guardar_raster, leer_raster, log, ruta_data


def _grilla_dem(cfg: dict):
    dem, transform, crs = leer_raster(ruta_data(cfg, "dem", "dem.tif"))
    return dem.shape, transform, crs


def _a_grilla_dem(cfg: dict, datos: np.ndarray, transform, crs) -> np.ndarray:
    forma, transform_dem, crs_dem = _grilla_dem(cfg)
    salida = np.zeros(forma, dtype="float32")
    reproject(
        datos.astype("float32"), salida,
        src_transform=transform, src_crs=crs,
        dst_transform=transform_dem, dst_crs=crs_dem,
        resampling=Resampling.bilinear,
    )
    return salida


def _ultimo_ciclo_gfs() -> datetime:
    """Último ciclo GFS plausiblemente publicado (~5 h de rezago)."""
    ahora = datetime.now(timezone.utc) - timedelta(hours=5)
    hora_ciclo = (ahora.hour // 6) * 6
    return ahora.replace(hour=hora_ciclo, minute=0, second=0, microsecond=0)


def descargar_gfs(cfg: dict) -> tuple[Path, dict]:
    """Suma APCP 6-horario 0→N horas y estima la isoterma 0 media del evento.

    En los GRIB pgrb2 de GFS, APCP en cada paso múltiplo de 6 h acumula las
    6 horas previas, por lo que la suma de f006, f012, …, f0NN da el total.
    """
    from herbie import Herbie

    horas = int(cfg["pronostico"]["horas"])
    o, s, e, n = cfg["region"]["bbox"]
    ciclo = _ultimo_ciclo_gfs()

    precip_total = None
    isotermas = []
    transform = crs = None
    ciclo_usado = None

    for intento in range(3):  # si el ciclo aún no está completo, retroceder 6 h
        ciclo_i = ciclo - timedelta(hours=6 * intento)
        try:
            for fxx in range(6, horas + 1, 6):
                H = Herbie(ciclo_i.strftime("%Y-%m-%d %H:00"), model="gfs",
                           product=cfg["pronostico"]["producto"], fxx=fxx, verbose=False)
                ds = H.xarray(":APCP:surface:", remove_grib=True)
                da = ds["tp"] if "tp" in ds else ds[list(ds.data_vars)[0]]
                # GFS usa longitudes 0–360
                sub = da.sel(latitude=slice(n + 0.5, s - 0.5),
                             longitude=slice(o % 360 - 0.5, e % 360 + 0.5))
                campo = sub.values
                if precip_total is None:
                    precip_total = np.zeros_like(campo, dtype="float64")
                    lons = sub.longitude.values
                    lats = sub.latitude.values
                    res = float(abs(lons[1] - lons[0]))
                    transform = rasterio.transform.from_origin(
                        (lons[0] % 360 + 180) % 360 - 180 - res / 2,
                        lats[0] + res / 2, res, res)
                    crs = "EPSG:4326"
                precip_total += campo

                if fxx % 12 == 0:  # isoterma 0 cada 12 h
                    try:
                        ds0 = H.xarray(":HGT:0C isotherm:", remove_grib=True)
                        v0 = ds0[list(ds0.data_vars)[0]]
                        sub0 = v0.sel(latitude=slice(n + 0.5, s - 0.5),
                                      longitude=slice(o % 360 - 0.5, e % 360 + 0.5))
                        isotermas.append(float(np.nanmean(sub0.values)))
                    except Exception:
                        pass
            ciclo_usado = ciclo_i
            break
        except Exception as exc:
            log.warning("Ciclo %s incompleto (%s); pruebo el anterior",
                        ciclo_i.isoformat(), exc)
            precip_total, isotermas = None, []

    if precip_total is None:
        raise RuntimeError("No se pudo descargar ningún ciclo GFS reciente")

    isoterma = float(np.mean(isotermas)) if isotermas else cfg["pronostico"]["isoterma0_defecto_m"]
    precip_dem = _a_grilla_dem(cfg, precip_total, transform, crs)

    destino = ruta_data(cfg, "forecast", "precip_mm.tif")
    guardar_raster(destino, precip_dem, _grilla_dem(cfg)[1], nodata=-9999)
    meta = {
        "fuente": "gfs",
        "ciclo": ciclo_usado.isoformat(),
        "horas": horas,
        "isoterma0_m": round(isoterma),
        "precip_max_mm": round(float(np.nanmax(precip_dem)), 1),
        "precip_media_mm": round(float(np.nanmean(precip_dem)), 1),
    }
    ruta_data(cfg, "forecast", "meta.json").write_text(json.dumps(meta, indent=2))
    log.info("GFS %s: máx %.0f mm / media %.0f mm en %d h, isoterma 0 ≈ %d m",
             meta["ciclo"], meta["precip_max_mm"], meta["precip_media_mm"],
             horas, meta["isoterma0_m"])
    return destino, meta


def descargar_ifs(cfg: dict) -> tuple[Path, dict]:
    """ECMWF IFS open-data 0.25°: tp acumulado a N horas + isoterma 0.

    En IFS open-data `tp` viene acumulado desde la inicialización, así que
    basta el paso final. La isoterma 0 se interpola del perfil medio t/gh en
    niveles de presión (el open-data no publica el nivel de congelación).
    """
    import xarray as xr
    from ecmwf.opendata import Client

    horas = int(cfg["pronostico"]["horas"])
    o, s, e, n = cfg["region"]["bbox"]
    cliente = Client(source="ecmwf", model="ifs", resol="0p25")

    tmp_tp = ruta_data(cfg, "forecast", "ifs_tp.grib2")
    r = cliente.retrieve(type="fc", param="tp", step=horas, target=str(tmp_tp))
    ciclo = r.datetime

    ds = xr.open_dataset(tmp_tp, engine="cfgrib",
                         backend_kwargs={"indexpath": ""})
    da = ds["tp"]
    lons = da.longitude.values
    if lons.max() > 180:  # normalizar 0–360 → -180–180
        da = da.assign_coords(longitude=(("longitude"),
                                         (lons + 180) % 360 - 180)).sortby("longitude")
    sub = da.sel(latitude=slice(n + 0.5, s - 0.5), longitude=slice(o - 0.5, e + 0.5))
    precip = sub.values * 1000.0  # m → mm
    res = float(abs(sub.longitude.values[1] - sub.longitude.values[0]))
    transform = rasterio.transform.from_origin(
        float(sub.longitude.values[0]) - res / 2,
        float(sub.latitude.values[0]) + res / 2, res, res)

    # isoterma 0: perfil medio del bbox con t/gh en niveles de presión
    isoterma = cfg["pronostico"]["isoterma0_defecto_m"]
    try:
        tmp_pl = ruta_data(cfg, "forecast", "ifs_pl.grib2")
        cliente.retrieve(type="fc", param=["t", "gh"], levelist=[1000, 925, 850, 700, 500],
                         step=horas // 2, target=str(tmp_pl))
        dpl = xr.open_dataset(tmp_pl, engine="cfgrib",
                              backend_kwargs={"indexpath": ""})
        lons_pl = dpl.longitude.values
        if lons_pl.max() > 180:
            dpl = dpl.assign_coords(longitude=(("longitude"),
                                               (lons_pl + 180) % 360 - 180)).sortby("longitude")
        perfil = dpl.sel(latitude=slice(n + 0.5, s - 0.5),
                         longitude=slice(o - 0.5, e + 0.5)).mean(
                             dim=["latitude", "longitude"])
        t = perfil["t"].values      # K, por nivel
        z = perfil["gh"].values     # m geopotencial
        orden = np.argsort(z)
        t, z = t[orden], z[orden]
        cruces = np.nonzero((t[:-1] - 273.15) * (t[1:] - 273.15) <= 0)[0]
        if cruces.size:
            i = cruces[-1]  # cruce más alto (perfil puede tener inversiones)
            frac = (273.15 - t[i]) / (t[i + 1] - t[i])
            isoterma = float(z[i] + frac * (z[i + 1] - z[i]))
    except Exception as exc:
        log.warning("Isoterma IFS no disponible (%s); uso %d m", exc, isoterma)

    precip_dem = _a_grilla_dem(cfg, precip, transform, "EPSG:4326")
    destino = ruta_data(cfg, "forecast", "precip_mm.tif")
    guardar_raster(destino, precip_dem, _grilla_dem(cfg)[1], nodata=-9999)
    meta = {
        "fuente": "ifs",
        "ciclo": ciclo.isoformat(),
        "horas": horas,
        "isoterma0_m": round(isoterma),
        "precip_max_mm": round(float(np.nanmax(precip_dem)), 1),
        "precip_media_mm": round(float(np.nanmean(precip_dem)), 1),
    }
    ruta_data(cfg, "forecast", "meta.json").write_text(json.dumps(meta, indent=2))
    log.info("IFS %s: máx %.0f mm / media %.0f mm en %d h, isoterma 0 ≈ %d m",
             meta["ciclo"], meta["precip_max_mm"], meta["precip_media_mm"],
             horas, meta["isoterma0_m"])
    return destino, meta


def descargar_pronostico(cfg: dict, modelo: str | None = None) -> tuple[Path, dict]:
    modelo = modelo or cfg["pronostico"]["modelo"]
    if modelo == "ifs":
        return descargar_ifs(cfg)
    return descargar_gfs(cfg)


def generar_escenario(cfg: dict, nombre: str) -> tuple[Path, dict]:
    """Campo uniforme de precipitación según un escenario de config.yaml."""
    esc = cfg["escenarios"][nombre]
    forma, transform, _ = _grilla_dem(cfg)
    campo = np.full(forma, float(esc["precipitacion_mm"]), dtype="float32")
    destino = ruta_data(cfg, "forecast", "precip_mm.tif")
    guardar_raster(destino, campo, transform, nodata=-9999)
    meta = {
        "fuente": f"escenario:{nombre}",
        "ciclo": None,
        "horas": esc["horas"],
        "isoterma0_m": esc["isoterma0_m"],
        "precip_max_mm": esc["precipitacion_mm"],
        "precip_media_mm": esc["precipitacion_mm"],
    }
    ruta_data(cfg, "forecast", "meta.json").write_text(json.dumps(meta, indent=2))
    log.info("Escenario '%s': %s mm / %s h, isoterma 0 = %s m",
             nombre, esc["precipitacion_mm"], esc["horas"], esc["isoterma0_m"])
    return destino, meta


if __name__ == "__main__":
    import sys
    cfg = cargar_config()
    if len(sys.argv) > 1:
        print(generar_escenario(cfg, sys.argv[1])[1])
    else:
        print(descargar_gfs(cfg)[1])
