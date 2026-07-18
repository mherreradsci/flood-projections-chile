"""Máscaras de agua Sentinel-1 GRD vía openEO (Copernicus Dataspace).

Para cada evento de calibración con fuente: sentinel1:
 1. Carga SENTINEL1_GRD (VV) en la ventana del evento sobre el bbox regional.
 2. sar_backscatter (sigma0-ellipsoid) y mínimo temporal server-side — el
    mínimo de retrodispersión de la ventana resalta el agua (superficie lisa).
 3. Descarga el GeoTIFF (job batch) y lo cachea en data/historical/.
 4. Localmente: umbral en dB → agua; se depuran océano (DEM < 1 m), agua
    permanente (WorldCover 80) y sombra de radar (HAND alto), y se guarda
    huella_<evento>.tif en la grilla del DEM (mismo formato que las GFD).

Requiere cuenta gratuita en dataspace.copernicus.eu. La autenticación OIDC
(device flow) abre una URL que el usuario debe visitar una vez; el refresh
token queda cacheado por openeo para las siguientes corridas.
"""

import numpy as np
import rasterio
from pathlib import Path
from rasterio.warp import Resampling, reproject

from .utils import cargar_config, guardar_raster, leer_raster, log, ruta_data


def conectar(cfg: dict):
    import openeo
    conn = openeo.connect(cfg["sentinel1"]["backend"])
    # device flow interactivo la primera vez; luego usa el refresh token cacheado
    conn.authenticate_oidc(max_poll_time=1800)
    return conn


TESELA_DEG = 0.6  # los jobs grandes hacen segfault en Orfeo; sync por teselas


def _teselas(cfg: dict) -> list[tuple[float, float, float, float]]:
    """Grilla de teselas del bbox que intersectan el polígono regional."""
    import geopandas as gpd
    from shapely.geometry import box

    from .aoi import obtener_region
    region = obtener_region(cfg).union_all()
    o, s, e, n = cfg["region"]["bbox"]
    teselas = []
    y = s
    while y < n:
        x = o
        while x < e:
            celda = (x, y, min(x + TESELA_DEG, e), min(y + TESELA_DEG, n))
            if box(*celda).intersects(region):
                teselas.append(celda)
            x += TESELA_DEG
        y += TESELA_DEG
    return teselas


def descargar_backscatter(cfg: dict, evento: dict, conn=None) -> Path | None:
    """Mínimo temporal de sigma0 VV (dB) del evento, por teselas síncronas."""
    destino = ruta_data(cfg, "historical", f"s1_min_vv_{evento['nombre']}.tif")
    if destino.exists():
        return destino
    if conn is None:
        conn = conectar(cfg)

    res_deg = cfg["sentinel1"]["resolucion_m"] / 111_320
    teselas = _teselas(cfg)
    dir_teselas = ruta_data(cfg, "historical", f"s1_teselas_{evento['nombre']}", "x").parent
    dir_teselas.mkdir(exist_ok=True)
    log.info("Sentinel-1 %s: %d teselas de %.1f°", evento["nombre"],
             len(teselas), TESELA_DEG)

    piezas = []
    for i, (o, s, e, n) in enumerate(teselas):
        pieza = dir_teselas / f"tesela_{i:02d}.tif"
        if pieza.exists():
            piezas.append(pieza)
            continue
        cubo = conn.load_collection(
            cfg["sentinel1"]["coleccion"],
            spatial_extent={"west": o, "south": s, "east": e, "north": n},
            temporal_extent=[evento["inicio"], evento["fin"]],
            bands=["VV"],
        )
        cubo = cubo.sar_backscatter(coefficient="sigma0-ellipsoid")
        cubo = cubo.reduce_dimension(dimension="t", reducer="min")
        cubo = cubo.apply(lambda x: 10 * x.log(base=10))  # lineal → dB
        cubo = cubo.resample_spatial(resolution=res_deg, projection=4326,
                                     method="average")
        try:
            cubo.download(str(pieza))
            piezas.append(pieza)
            log.info("  tesela %d/%d OK", i + 1, len(teselas))
        except Exception as exc:
            # sin escenas en la tesela/ventana o error transitorio del backend
            log.warning("  tesela %d/%d falló: %s", i + 1, len(teselas),
                        str(exc)[:150])

    if not piezas:
        log.warning("Sentinel-1 %s: ninguna tesela con datos", evento["nombre"])
        return None

    from rasterio.merge import merge
    fuentes = [rasterio.open(p) for p in piezas]
    mosaico, transform = merge(fuentes, nodata=np.nan)
    perfil = {
        "driver": "GTiff", "height": mosaico.shape[1], "width": mosaico.shape[2],
        "count": 1, "dtype": "float32", "crs": fuentes[0].crs,
        "transform": transform, "nodata": np.nan, "compress": "deflate",
    }
    for f in fuentes:
        f.close()
    with rasterio.open(destino, "w", **perfil) as dst:
        dst.write(mosaico[0].astype("float32"), 1)
    log.info("Backscatter %s: mosaico de %d/%d teselas → %s",
             evento["nombre"], len(piezas), len(teselas), destino.name)
    return destino


def _db_en_grilla(cfg: dict, nombre: str, forma, transform_dem, crs_dem):
    """Backscatter mínimo del evento reproyectado a la grilla del DEM."""
    bruto = ruta_data(cfg, "historical", f"s1_min_vv_{nombre}.tif")
    if not bruto.exists():
        return None
    with rasterio.open(bruto) as src:
        db = np.full(forma, np.nan, dtype="float32")
        reproject(rasterio.band(src, 1), db,
                  dst_transform=transform_dem, dst_crs=crs_dem,
                  resampling=Resampling.average,
                  dst_nodata=np.nan)
    return db


def _oscuro_permanente(cfg: dict, evento: dict, forma, transform_dem,
                       crs_dem) -> np.ndarray | None:
    """Celdas oscuras en TODOS los demás eventos S1 con dato: superficie lisa
    permanente al radar (salares, arenales), no inundación.

    La inundación de un evento no persiste en los otros (años de distancia),
    así que "oscuro siempre" delata el falso positivo clásico del umbral dB
    en desierto. Solo se descarta donde algún otro evento tiene dato válido;
    sin evidencia no se asume permanencia.
    """
    umbral = float(cfg["sentinel1"]["umbral_db"])
    otros = [e for e in cfg["calibracion"]["eventos"]
             if e.get("fuente") == "sentinel1" and e["nombre"] != evento["nombre"]]
    visto = confirmado = None
    for ev in otros:
        db = _db_en_grilla(cfg, ev["nombre"], forma, transform_dem, crs_dem)
        if db is None:
            continue
        valido = np.isfinite(db)
        no_contradice = (db < umbral) | ~valido
        visto = valido if visto is None else (visto | valido)
        confirmado = no_contradice if confirmado is None \
            else (confirmado & no_contradice)
    if visto is None:
        return None
    return visto & confirmado


def derivar_mascara(cfg: dict, evento: dict) -> Path | None:
    """Umbral dB + depuración → huella_<evento>.tif en la grilla del DEM."""
    destino = ruta_data(cfg, "historical", f"huella_{evento['nombre']}.tif")
    if destino.exists():
        return destino

    dem, transform_dem, crs_dem = leer_raster(ruta_data(cfg, "dem", "dem.tif"))
    db = _db_en_grilla(cfg, evento["nombre"], dem.shape, transform_dem, crs_dem)
    if db is None:
        return None

    validas = int(np.isfinite(db).sum())
    if validas == 0:
        log.warning("Backscatter %s sin datos (¿sin escenas en la ventana?)",
                    evento["nombre"])
        return None

    agua = np.isfinite(db) & (db < float(cfg["sentinel1"]["umbral_db"]))
    # depuración de falsos positivos
    agua &= dem >= 1.0                                     # océano/playa
    ruta_lc = ruta_data(cfg, "landcover", "worldcover.tif")
    if ruta_lc.exists():
        agua &= leer_raster(ruta_lc)[0] != 80              # agua permanente
    ruta_hand = ruta_data(cfg, "dem", "hand.tif")
    if ruta_hand.exists():
        hand = leer_raster(ruta_hand)[0]
        agua &= (hand >= 0) & (hand < cfg["sentinel1"]["hand_max_filtro_m"])
    if cfg["sentinel1"].get("filtrar_oscuro_permanente"):
        perm = _oscuro_permanente(cfg, evento, dem.shape, transform_dem, crs_dem)
        if perm is not None:
            antes = int(agua.sum())
            agua &= ~perm
            log.info("Filtro oscuro permanente %s: %d celdas descartadas",
                     evento["nombre"], antes - int(agua.sum()))

    if agua.sum() == 0:
        log.warning("Máscara %s vacía tras depurar; revisar umbral_db",
                    evento["nombre"])
        return None
    guardar_raster(destino, agua.astype("uint8"), transform_dem, nodata=255,
                   dtype="uint8")
    cobertura = 100 * validas / dem.size
    log.info("Huella %s (Sentinel-1): %d celdas de agua (cobertura de escenas "
             "%.0f%% del raster)", evento["nombre"], int(agua.sum()), cobertura)
    return destino


def preparar_mascaras_s1(cfg: dict) -> dict[str, Path]:
    eventos = [e for e in cfg["calibracion"]["eventos"]
               if e.get("fuente") == "sentinel1"]
    pendientes = [e for e in eventos
                  if not ruta_data(cfg, "historical",
                                   f"huella_{e['nombre']}.tif").exists()]
    conn = None
    rutas = {}
    for ev in eventos:
        try:
            if ev in pendientes and not ruta_data(
                    cfg, "historical", f"s1_min_vv_{ev['nombre']}.tif").exists():
                if conn is None:
                    conn = conectar(cfg)
                descargar_backscatter(cfg, ev, conn)
            ruta = derivar_mascara(cfg, ev)
            if ruta:
                rutas[ev["nombre"]] = ruta
        except Exception as exc:
            log.warning("Sentinel-1 %s falló: %s", ev["nombre"], exc)
    return rutas


if __name__ == "__main__":
    cfg = cargar_config()
    print(preparar_mascaras_s1(cfg))
