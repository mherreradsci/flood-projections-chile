# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Qué es

Sistema en Python para proyectar anegamientos en la Región de Coquimbo ante precipitación extrema, con un modelo semi-hidrológico HAND calibrado (DEM Copernicus → HAND con pysheds; lluvia GFS/IFS o escenario sintético filtrada por isoterma 0; escorrentía SCS-CN por subcuenca HydroBASINS; distribución de volumen estilo FwDET; calibración contra huellas históricas). Producto principal: mapa folium en `outputs/`. Todo el código, comentarios, commits y salidas están en español — mantener esa convención.

## Comandos

No hay tests ni linter configurados. El entorno vive en `.venv`:

```bash
python3 -m venv .venv && .venv/bin/pip install -r requirements.txt

# Pipeline (en orden; cada paso cachea sus salidas y se salta si ya existen)
.venv/bin/python scripts/01_descargar_datos.py            # insumos + GFS vigente (--sin-pronostico para omitir GFS)
.venv/bin/python scripts/02_preparar_terreno.py           # flujo/drenaje/HAND (lento la primera vez)
.venv/bin/python scripts/03_calibrar.py                   # → data/calibracion.json + outputs/calibracion_reporte.csv
.venv/bin/python scripts/04_proyectar.py --fuente gfs     # también: ifs | escenario --escenario extremo_200mm
```

Para ejecutar módulos de la librería de forma directa (los scripts hacen `sys.path.insert` de `src/`; el paquete no está instalado):

```bash
PYTHONPATH=src .venv/bin/python -c "from inundaciones.utils import cargar_config; ..."
```

## Arquitectura

- **`config.yaml` es la única fuente de configuración.** Todos los módulos reciben el dict `cfg` de `utils.cargar_config()`; no hay constantes de dominio dispersas en el código. Parámetros del modelo (CN por clase WorldCover, umbrales HAND, eventos de calibración, escenarios sintéticos) se cambian ahí, no en el código.
- **Multi-región:** los cuatro scripts aceptan `--config config_<region>.yaml`. Si el config define `region.id`, `data/` y `outputs/` ganan una subcarpeta por región (`data/<id>/`), lo que permite corridas paralelas en el mismo checkout. El config de Coquimbo mantiene `id` comentado para conservar el layout plano que usa el cron; activarlo exige mover `data/*` y `outputs/*` a las subcarpetas.
- **`scripts/0N_*.py` son wrappers delgados; la lógica vive en `src/inundaciones/`.** Módulos `ingest_*` traen datos externos (DEM, WorldCover, GFD, GFS/IFS, Sentinel-1 vía openEO); `terrain` → `runoff` → `flood_model` → `new_areas`/`exposure` → `mapa` es la cadena de modelado; `calibrate` produce los factores de volumen que `runoff` consume.
- **Contrato de grilla:** todos los rasters intermedios y finales se remuestrean a la grilla del DEM (`data/dem/dem.tif`, EPSG:4326). Cualquier dato nuevo debe pasar por ese remuestreo antes de operar celda a celda.
- **Caché por existencia de archivo:** los pasos costosos (HAND, descargas, huellas) devuelven temprano si su salida ya existe en `data/`. Para forzar un recálculo hay que borrar el archivo (p. ej. `data/dem/hand.tif` tras cambiar `umbral_drenaje_km2`). `data/` y `outputs/` están en `.gitignore`.
- **Convención `sufijo`:** las salidas de `04_proyectar.py` se nombran por fuente de lluvia (`extension_gfs.tif`, `zonas_nuevas_extremo_200mm.geojson`, …), lo que permite mantener corridas GFS/IFS/escenario en paralelo. Los mapas HTML además llevan ciclo del pronóstico y timestamp.
- **Contrato de huellas históricas:** cada evento de calibración termina como `data/historical/huella_<nombre>.tif` (máscara 0/1 en grilla DEM), sin importar si viene de GFD (MODIS) o Sentinel-1 (openEO). `huella_historica_union.tif` es la unión que usa `new_areas` para separar zonas nuevas de recurrentes.

## Decisiones de calibración (no "corregir" sin contexto)

- El objetivo de calibración NO es maximizar CSI celda a celda: contra MODIS 250 m eso es estructuralmente bajo y amplifica ruido. `calibrate.py` usa el percentil 80 del HAND observado por subcuenca (corredor que captura ~80% de la observación) y deriva de ahí el factor de volumen; CSI/POD/FAR se reportan solo como referencia.
- Subcuencas sin observación suficiente heredan la mediana regional de los factores — la ausencia de detección MODIS no implica ausencia de inundación.
- El modelo sobrepredice extensión a propósito: es un producto de susceptibilidad, no un mapa de certeza.
- Sentinel-1 (`ingest_sentinel1.py`) requiere cuenta gratuita en dataspace.copernicus.eu; la autenticación OIDC es interactiva la primera vez y openeo cachea el refresh token.
- La exposición OSM usa el espejo `overpass.kumi.systems` (overpass-api.de banea consultas masivas) y es tolerante a fallos: si Overpass falla, el mapa se genera sin esa capa.
