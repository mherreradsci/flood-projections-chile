#!/usr/bin/env python
"""Reprocesa ciclos IFS pasados puntuales (backfill) sin pisar el mapa publicado.

Uso:
  python scripts/reprocesar_ciclo_ifs.py --ciclo 2026-07-27T00
  python scripts/reprocesar_ciclo_ifs.py --ciclo 2026-07-27T00 --ciclo 2026-07-27T06
  python scripts/reprocesar_ciclo_ifs.py --ciclo 2026-07-27T12 --config config_atacama.yaml
  python scripts/reprocesar_ciclo_ifs.py --ciclo 2026-07-29T18 --publicar
  python scripts/reprocesar_ciclo_ifs.py --ciclo 2026-07-27T00 --forzar

Para cuándo sirve: mismo caso que `reprocesar_ciclo_gfs.py` pero para IFS —
huecos en `outputs/<region>/` para ciclos que sí llegaron a publicarse en
ECMWF open-data mientras el cron estuvo caído. `04_proyectar.py --fuente ifs`
solo sabe pedir "el ciclo más reciente"; este script fuerza un ciclo puntual
pasando `ciclo` a `ingest_forecast.descargar_ifs`, que ya sabe traducirlo a
`date`/`time` para el cliente de ECMWF (no hace falta monkeypatch: a
diferencia de GFS, "más reciente" para IFS es resuelto por el cliente mismo,
no por una heurística nuestra).

El open-data de IFS retiene solo ~3-4 días (medido: 2026-07-27 en adelante
respondía 200, 2026-07-26 y anteriores ya daban 404 en las tres réplicas —
AWS/Azure/GCS espejan la misma ventana rodante, no un archivo largo). Un
ciclo fuera de esa ventana falla con `HTTPError 404`, no con un ciclo
distinto silencioso: `reprocesar` valida que el `ciclo` en el meta devuelto
coincida exactamente con el pedido.

El mismo 404 también sale por el otro extremo: un ciclo del día todavía sin
publicar (medido 2026-07-30: el 06z publicó sus GRIB recién a las 12:27 UTC,
+6h27m sobre el nominal; a las 18:28 UTC el 12z aún solo tenía el bufr de
trayectorias de ciclones, sin `tp`/`pl`). No confundir con la ventana de
retención de arriba — acá el ciclo simplemente no existe todavía en origen.
Ojo que esto NO aplica a `04_proyectar.py --fuente ifs` (sin `--ciclo`): el
cliente de ECMWF resuelve "más reciente" retrocediendo de a 6h con HEAD
requests (`client.py::latest()`) y nunca pide un ciclo inexistente; el 404
por publicación tardía solo puede darse acá, al forzar `date`/`time`
explícitos. Como referencia, ~8h después de la hora nominal del ciclo es un
margen razonable antes de reintentar un backfill del ciclo del día — sin
garantía dura, ECMWF puede demorarse más.

Por defecto OMITE los ciclos que ya tienen mapa en `outputs/<region>/`: el
nombre del HTML lleva ciclo *y* timestamp de render, así que reprocesar no
reemplaza el archivo previo sino que agrega otro para el mismo ciclo. La
comprobación corre antes de descargar el GRIB, de modo que omitir no cuesta
red. Con `--forzar` se genera el render adicional igual.

Por defecto NO publica: no toca `publicacion/<region>/mapa_ifs.html`, el
mapa vivo que consume el carrusel externo, porque se asume que se está
rellenando historia y no reemplazando el ciclo vigente. Para el ciclo que
sí es el vigente real, pasar --publicar (y volver a correr
`04_proyectar.py --fuente ifs` después si además se reprocesaron ciclos
más viejos, para que el mapa publicado quede en el ciclo correcto).
"""

import argparse
import sys
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from inundaciones import ingest_forecast
from inundaciones.calibrate import cargar_factores
from inundaciones.exposure import evaluar_exposicion
from inundaciones.flood_model import modelar_inundacion
from inundaciones.mapa import generar_mapa
from inundaciones.new_areas import identificar_zonas_nuevas
from inundaciones.publicar import publicar_mapa
from inundaciones.runoff import calcular_escorrentia
from inundaciones.utils import (
    activar_log_a_archivo,
    cargar_config,
    log,
    mapas_de_ciclo,
    validar_ciclo_no_futuro,
)


def _parse_ciclo(texto: str) -> datetime:
    dt = datetime.fromisoformat(texto)
    return dt.replace(tzinfo=timezone.utc) if dt.tzinfo is None else dt.astimezone(timezone.utc)


def reprocesar(cfg: dict, ciclo: datetime, *, publicar: bool, sin_exposicion: bool) -> Path:
    """Descarga y modela un ciclo IFS puntual (no necesariamente el más reciente)."""
    _, meta = ingest_forecast.descargar_ifs(cfg, ciclo=ciclo)

    # el cliente de ECMWF devuelve un datetime naive (sin tzinfo) y así queda
    # en meta["ciclo"]; comparar contra la forma naive del ciclo pedido, no
    # contra ciclo.isoformat() (que lleva "+00:00" y nunca matchearía)
    if meta["ciclo"] != ciclo.strftime("%Y-%m-%dT%H:%M:%S"):
        raise RuntimeError(
            f"Pedí el ciclo {ciclo.isoformat()} pero la descarga cayó en "
            f"{meta['ciclo']} (ciclo incompleto en ECMWF o ya fuera de su "
            "ventana de retención); no reproceso con un ciclo distinto al pedido."
        )

    factores = cargar_factores(cfg)
    volumenes = calcular_escorrentia(cfg, factores=factores, sufijo="ifs")
    resultado = modelar_inundacion(cfg, volumenes, sufijo="ifs")
    identificar_zonas_nuevas(cfg, sufijo="ifs")
    if not sin_exposicion:
        try:
            evaluar_exposicion(cfg, sufijo="ifs")
        except Exception as exc:
            log.warning("Exposición OSM falló (%s); el mapa se genera sin ella", exc)
    mapa = generar_mapa(cfg, sufijo="ifs")
    if publicar:
        publicar_mapa(cfg, mapa, "ifs")
    log.info("Ciclo %s -> %.1f km² | %s", ciclo.isoformat(), resultado["area_km2"], mapa)
    return mapa


def main():
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--ciclo", action="append", required=True,
                        help="ciclo IFS a reprocesar, ISO UTC (p. ej. 2026-07-27T06); repetible")
    parser.add_argument("--config", default=None,
                        help="config alternativo por región (p. ej. config_atacama.yaml)")
    parser.add_argument("--publicar", action="store_true",
                        help="además actualiza publicacion/<region>/mapa_ifs.html (por defecto no)")
    parser.add_argument("--sin-exposicion", action="store_true",
                        help="omite la consulta OSM de infraestructura expuesta")
    parser.add_argument("--forzar", action="store_true",
                        help="reprocesa aunque el ciclo ya tenga mapa en outputs/ "
                             "(genera un render adicional, no reemplaza el previo)")
    args = parser.parse_args()

    cfg = cargar_config(args.config)
    ruta_log = activar_log_a_archivo(cfg, "reprocesar_ifs")
    log.info("== inicio == (log: %s)", ruta_log)
    try:
        hechos, omitidos = 0, 0
        for texto in args.ciclo:
            ciclo = _parse_ciclo(texto)
            validar_ciclo_no_futuro(ciclo)
            previos = mapas_de_ciclo(cfg, "ifs", ciclo.isoformat())
            if previos and not args.forzar:
                log.warning("Ciclo %s ya tiene %d mapa(s) en outputs/ (%s); lo omito. "
                            "Usar --forzar para generar otro render de todos modos.",
                            ciclo.isoformat(), len(previos), previos[-1].name)
                omitidos += 1
                continue
            if previos:
                log.warning("Ciclo %s ya tenía %d mapa(s); --forzar: se agrega otro render.",
                            ciclo.isoformat(), len(previos))
            reprocesar(cfg, ciclo, publicar=args.publicar,
                       sin_exposicion=args.sin_exposicion)
            hechos += 1

        log.info("Reprocesados %d ciclo(s), omitidos %d por mapa existente", hechos, omitidos)
    except Exception:
        log.exception("Corrida abortada por un error")
        raise
    finally:
        log.info("== fin ==")


if __name__ == "__main__":
    main()
