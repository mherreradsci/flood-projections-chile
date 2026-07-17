"""Hidrología de terreno con pysheds: flujo, red de drenaje y HAND.

Cómo se calcula por dónde correría el agua
------------------------------------------
El cálculo opera celda a celda sobre el DEM (modelo digital de elevación,
~30 m por celda) bajo un principio único: el agua se mueve hacia la celda
vecina más baja. La cadena tiene cinco pasos:

1. Acondicionar el DEM (``fill_pits``, ``fill_depressions``,
   ``resolve_flats``): el DEM crudo trae hoyos falsos y zonas planas donde
   el flujo quedaría atrapado por errores de medición; se rellenan y se les
   impone una pendiente mínima para que todo el terreno drene hacia alguna
   salida.
2. Dirección de flujo (``flowdir``, método D8): para cada celda se miran
   sus 8 vecinas y se anota hacia cuál bajaría el agua. El resultado es un
   campo de direcciones — cada celda apunta cuesta abajo.
3. Acumulación (``accumulation``): siguiendo esas direcciones se cuenta
   cuántas celdas aguas arriba desembocan en cada celda. Una cumbre vale 1
   (solo su propia lluvia); el fondo de una quebrada puede recibir millones.
4. Red de drenaje: donde la acumulación supera el umbral
   ``terreno.umbral_drenaje_km2`` (config.yaml) se declara cauce. Eso
   dibuja ríos y quebradas, incluidos los secos casi todo el año.
5. HAND (``compute_hand``): para cada celda se sigue su dirección de flujo
   hasta el cauce más cercano y se calcula cuántos metros más arriba está
   la celda respecto de ese cauce. Es la variable clave del modelo: si la
   crecida sube N metros, se anegan las celdas con HAND < N. Nótese que no
   es altura sobre el mar sino sobre el cauce al que se drena — una terraza
   a 800 msnm junto a un río puede ser más riesgosa que un cerro a 200 msnm.

El paso es el más lento del pipeline y por eso se cachea por existencia de
archivo: cambiar el umbral de drenaje exige borrar data/dem/hand.tif.

Salidas cacheadas en data/dem/:
  acc.tif    — acumulación de flujo (nº de celdas aguas arriba)
  streams.tif— máscara de red de drenaje (uint8)
  hand.tif   — Height Above Nearest Drainage (m)
"""

from pathlib import Path

import numpy as np

from .utils import (area_celda_m2, cargar_config, guardar_raster, leer_raster,
                    log, ruta_data)


def preparar_terreno(cfg: dict) -> dict[str, Path]:
    ruta_hand = ruta_data(cfg, "dem", "hand.tif")
    ruta_acc = ruta_data(cfg, "dem", "acc.tif")
    ruta_streams = ruta_data(cfg, "dem", "streams.tif")
    if ruta_hand.exists() and ruta_acc.exists() and ruta_streams.exists():
        return {"hand": ruta_hand, "acc": ruta_acc, "streams": ruta_streams}

    from pysheds.grid import Grid

    ruta_dem = ruta_data(cfg, "dem", "dem.tif")
    log.info("Cargando DEM en pysheds…")
    grid = Grid.from_raster(str(ruta_dem))
    dem = grid.read_raster(str(ruta_dem))

    log.info("Acondicionando DEM (pits, depresiones, planicies)…")
    dem_sin_pits = grid.fill_pits(dem)
    dem_sin_dep = grid.fill_depressions(dem_sin_pits)
    dem_cond = grid.resolve_flats(dem_sin_dep)

    log.info("Direcciones y acumulación de flujo…")
    fdir = grid.flowdir(dem_cond)
    acc = grid.accumulation(fdir)

    # umbral de cauce: km² → nº de celdas
    _, transform, _ = leer_raster(ruta_dem)
    lat_media = (cfg["region"]["bbox"][1] + cfg["region"]["bbox"][3]) / 2
    celda_m2 = area_celda_m2(transform, lat_media)
    umbral_celdas = int(cfg["terreno"]["umbral_drenaje_km2"] * 1e6 / celda_m2)
    streams = acc > umbral_celdas  # comparación sobre Raster pysheds (no ndarray)
    log.info("Umbral de cauce: %d celdas (%.0f m²/celda); celdas de cauce: %d",
             umbral_celdas, celda_m2, int(np.asarray(streams).sum()))

    log.info("Calculando HAND…")
    hand = grid.compute_hand(fdir, dem_cond, streams)

    hand_arr = np.asarray(hand, dtype="float32")
    hand_arr[~np.isfinite(hand_arr)] = -9999

    # océano y agua permanente no son "inundables": HAND nodata ahí.
    # (el DEM tiene el mar en 0 m y HAND≈0, lo que anega la costa en falso)
    dem_arr, _, _ = leer_raster(ruta_dem)
    hand_arr[dem_arr < 0.5] = -9999
    ruta_lc = ruta_data(cfg, "landcover", "worldcover.tif")
    if ruta_lc.exists():
        lc = leer_raster(ruta_lc)[0]
        hand_arr[(lc == 80) | (lc == 0)] = -9999

    guardar_raster(ruta_hand, hand_arr, transform, nodata=-9999)
    guardar_raster(ruta_acc, np.asarray(acc, dtype="float32"), transform, nodata=-9999)
    guardar_raster(ruta_streams, streams.astype("uint8"), transform, nodata=255,
                   dtype="uint8")
    log.info("Terreno listo: HAND válido en %.1f%% de celdas",
             100 * float((hand_arr >= 0).mean()))
    return {"hand": ruta_hand, "acc": ruta_acc, "streams": ruta_streams}


if __name__ == "__main__":
    cfg = cargar_config()
    print(preparar_terreno(cfg))
