# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Qué es

Sistema en Python para proyectar anegamientos en la Región de Coquimbo ante precipitación extrema, con un modelo semi-hidrológico HAND calibrado (DEM Copernicus → HAND con pysheds; lluvia GFS/IFS o escenario sintético filtrada por isoterma 0; escorrentía SCS-CN por subcuenca HydroBASINS; distribución de volumen estilo FwDET; calibración contra huellas históricas). Producto principal: mapa folium en `outputs/`. Todo el código, comentarios, commits y salidas están en español — mantener esa convención.

## Comandos

El entorno vive en `.venv`. Linter (Ruff) y tests (pytest) son dependencias de desarrollo, separadas en `requirements-dev.txt`:

```bash
python3 -m venv .venv && .venv/bin/pip install -r requirements.txt
.venv/bin/pip install -r requirements-dev.txt   # solo para lintear/testear

# Pipeline (en orden; cada paso cachea sus salidas y se salta si ya existen)
.venv/bin/python scripts/01_descargar_datos.py            # insumos + GFS vigente (--sin-pronostico para omitir GFS)
.venv/bin/python scripts/02_preparar_terreno.py           # flujo/drenaje/HAND (lento la primera vez)
.venv/bin/python scripts/03_calibrar.py                   # → data/calibracion.json + outputs/calibracion_reporte.csv
.venv/bin/python scripts/04_proyectar.py --fuente gfs     # también: ifs | escenario --escenario extremo_200mm

# Poda de outputs/ (simula por default; --aplicar borra, y no hay vuelta atrás)
.venv/bin/python scripts/05_limpiar_outputs.py --aplicar               # renders duplicados del mismo ciclo
.venv/bin/python scripts/05_limpiar_outputs.py --conservar-ciclos 8 --aplicar   # además, poda ciclos viejos

.venv/bin/ruff check .    # lint (config en pyproject.toml); --fix aplica lo autocorregible
.venv/bin/pytest          # tests unitarios en tests/, sin datos reales (ver más abajo)
```

`tests/` tiene dos categorías. La mayoría son funciones puras de matemática/numpy/pandas fáciles
de romper en silencio (`flood_model._umbral_para_volumen`, `calibrate._metricas`,
`utils.area_celda_m2`) usando arrays sintéticos, sin huellas ni rasters reales. Además,
`test_*_integracion.py` ejercita funciones de orquestación que sí hacen I/O real mínimo —
escriben/leen GeoTIFF sintéticos en `tmp_path` (vía `utils.guardar_raster`/`leer_raster`) para
probar el cruce por id de subcuenca u otro "wiring" que un test de función pura no atrapa
(`test_flood_model_integracion.py::modelar_inundacion`). El truco para evitar red/datos reales:
aprovechar la caché por existencia de archivo (ver más abajo) precreando el archivo cacheado
(p. ej. `subcuencas_id.tif`) para que la función corte antes de llegar a la parte de I/O externo.
Esto solo funciona si el límite de caché coincide con el límite de lo no testeable; no todas las
funciones de orquestación se prestan para esto. El resto del pipeline (`terrain`, `ingest_*`)
depende de DEM/GFS/Sentinel-1 reales y de `pysheds`/`openeo`, por lo que no tiene tests
automatizados; se verifica corriendo el pipeline. Al agregar una función nueva sin I/O ni
dependencias externas, sumarle un test en `tests/`.

Para ejecutar módulos de la librería de forma directa (los scripts hacen `sys.path.insert` de `src/`; el paquete no está instalado):

```bash
PYTHONPATH=src .venv/bin/python -c "from inundaciones.utils import cargar_config; ..."
```

## Arquitectura

- **`config.yaml` es la única fuente de configuración.** Todos los módulos reciben el dict `cfg` de `utils.cargar_config()`; no hay constantes de dominio dispersas en el código. Parámetros del modelo (CN por clase WorldCover, umbrales HAND, eventos de calibración, escenarios sintéticos) se cambian ahí, no en el código.
- **Multi-región:** los cuatro scripts aceptan `--config config_<region>.yaml`. `region.id` define la subcarpeta por región dentro de `data/` y `outputs/` (`data/coquimbo/`, `data/atacama/`, …), lo que permite corridas paralelas en el mismo checkout. Todas las regiones usan el layout por subcarpeta; un config sin `id` caería al layout plano legado, evitarlo.
- **`scripts/0N_*.py` son wrappers delgados; la lógica vive en `src/inundaciones/`.** Módulos `ingest_*` traen datos externos (DEM, WorldCover, GFD, GFS/IFS, Sentinel-1 vía openEO); `terrain` → `runoff` → `flood_model` → `new_areas`/`exposure` → `mapa` es la cadena de modelado; `calibrate` produce los factores de volumen que `runoff` consume.
- **Contrato de grilla:** todos los rasters intermedios y finales se remuestrean a la grilla del DEM (`data/dem/dem.tif`, EPSG:4326). Cualquier dato nuevo debe pasar por ese remuestreo antes de operar celda a celda.
- **Caché por existencia de archivo:** los pasos costosos (HAND, descargas, huellas) devuelven temprano si su salida ya existe en `data/`. Para forzar un recálculo hay que borrar el archivo (p. ej. `data/dem/hand.tif` tras cambiar `umbral_drenaje_km2`). `data/` y `outputs/` están en `.gitignore`.
- **Convención `sufijo`:** las salidas de `04_proyectar.py` se nombran por fuente de lluvia (`extension_gfs.tif`, `zonas_nuevas_extremo_200mm.geojson`, …), lo que permite mantener corridas GFS/IFS/escenario en paralelo. También `data/forecast/` es por sufijo (`precip_mm_<sufijo>.tif`, `meta_<sufijo>.json`, helpers `ingest_forecast.ruta_precip/ruta_meta`), así `generar_mapa`/`calcular_escorrentia` nunca mezclan la lluvia de una fuente con los rasters de otra. Los mapas HTML además llevan ciclo del pronóstico y timestamp.
- **Publicación externa:** `04_proyectar.py` termina copiando el mapa a `publicacion/<region>/mapa_<sufijo>.html` (nombre estable, sobrescribe la corrida anterior de esa fuente) vía `publicar.publicar_mapa`, y mantiene `publicacion/manifest.json` — índice global con regiones e ítems (fuente, ciclo, acumulados) que consume el servicio de carrusel externo. `outputs/` sigue siendo el historial timestampeado; `publicacion/` está en `.gitignore`.
- **Poda de `outputs/` (`limpieza.py` + `scripts/05_limpiar_outputs.py`):** el historial crece sin techo (~4 MB por HTML) y además acumula *duplicados por ciclo*, porque el nombre lleva ciclo **y** timestamp de render: volver a proyectar un ciclo agrega un archivo en vez de reemplazarlo. `reprocesar_ciclo_gfs.py` evita crearlos avisando, pero la ruta del cron (`04_proyectar.py`) no. La poda de duplicados no pierde información (los renders viejos del mismo ciclo son intentos superados); `--conservar-ciclos N` sí descarta historial, por eso es opt-in. Todo simula por default: `--aplicar` es obligatorio para borrar, ya que `outputs/` está en `.gitignore` y no se recupera. `limpieza.PATRON_MAPA` es la dirección inversa de `utils.ciclo_tag` — si se cambia el formato del tag hay que tocar ambos, y `tests/test_limpieza.py` los ata componiendo uno con otro.
- **Contrato de huellas históricas:** cada evento de calibración termina como `data/historical/huella_<nombre>.tif` (máscara 0/1 en grilla DEM), sin importar si viene de GFD (MODIS) o Sentinel-1 (openEO). `huella_historica_union.tif` es la unión que usa `new_areas` para separar zonas nuevas de recurrentes.
- **CI (`.github/workflows/ci.yml`):** en push/PR corre ruff + pytest y, solo en push a `main`, regenera `docs/coverage.svg` y lo commitea de vuelta con `[skip ci]` si cambió. GitHub matchea `[skip ci]` en todo el mensaje del commit (subject + body), no solo como directiva al inicio — evitar esa cadena literal en mensajes que describan el mecanismo en prosa, o el commit se autoexcluye de CI sin aviso (ya pasó una vez).

## Decisiones de calibración (no "corregir" sin contexto)

- El objetivo de calibración NO es maximizar CSI celda a celda: contra MODIS 250 m eso es estructuralmente bajo y amplifica ruido. `calibrate.py` usa el percentil 80 del HAND observado por subcuenca (corredor que captura ~80% de la observación) y deriva de ahí el factor de volumen; CSI/POD/FAR se reportan solo como referencia.
- Subcuencas sin observación suficiente heredan la mediana regional de los factores — la ausencia de detección MODIS no implica ausencia de inundación.
- El modelo sobrepredice extensión a propósito: es un producto de susceptibilidad, no un mapa de certeza.
- **Agregación de P/CN en `runoff.calcular_escorrentia` (medido 2026-07-28; no es un bug):** se promedia P y CN sobre la máscara pluvial y recién ahí se aplica SCS-CN. Parece un error de orden (`mean-then-transform ≠ transform-then-mean`), pero contra los rásters reales el sesgo de volumen es **−0.1% en eventos fuertes** (IFS 187 mm) y no mueve la extensión mapeada: el CV espacial de P dentro de una subcuenca es ~0.055 (GFS/IFS a 0.25° remuestreado a 90 m es liso por construcción, no hay varianza sub-subcuenca que perder) y la curvatura de SCS-CN `Q'' = 2S²/(P+0.8S)³` decae como P⁻³.
- **El sesgo real está en el quiebre de `Ia`, no en la curvatura.** `_escorrentia_mm` devuelve `0.0` duro si `P ≤ Ia = 0.2S` (12.7 mm con CN 80), así que promediar P primero puede apagar una subcuenca completa donde algunas celdas sí superan Ia. Régimen medido reescalando el campo IFS real: 60 mm → −2% volumen / +0.9% área; 30 mm → −8% / +2%; 20 mm → −20% / **+10.6% (+52 km²)**; 15 mm → −42%; ≤10 mm → −94% a −100%. Bajo ~13 mm/72 h el modelo estructuralmente no puede producir salida: por eso en temporada seca (GFS de 1.9 mm) da Q=0 en las 42 subcuencas de Coquimbo y las 101 de Atacama — es Ia funcionando como está diseñado, no una falla del pipeline.
- **La calibración no absorbe ese sesgo:** `calibracion.eventos` usa P uniforme de 70–110 mm, justo el régimen donde la agregación es exacta (<1%). Los factores se ajustan ahí y luego se aplican en cualquier régimen. Si alguna vez se decide cubrir el rango 15–40 mm, vectorizar `_escorrentia_mm` sobre la máscara pluvial (barato, ya es una op numpy enmascarada) y **re-correr `03_calibrar.py`** para que los factores queden consistentes con la nueva integración. No cambiar a per-celda a ciegas: SCS-CN es un método empírico agregado (pensado para CN promedio de cuenca), aplicarlo celda a celda produce sistemáticamente más escorrentía y es una extrapolación, no la referencia verdadera.
- Sentinel-1 (`ingest_sentinel1.py`) requiere cuenta gratuita en dataspace.copernicus.eu; la autenticación OIDC es interactiva la primera vez y openeo cachea el refresh token.
- `sentinel1.filtrar_oscuro_permanente` descarta celdas oscuras en todos los eventos S1 (salares y arenales lisos al radar). Sin él, en Atacama la mitad del "agua" es salar y los factores de calibración saturan en factor_max. Activo en Atacama, apagado en Coquimbo (huellas ya calibradas).
- La exposición OSM usa el espejo `overpass.kumi.systems` (overpass-api.de banea consultas masivas) y es tolerante a fallos: si Overpass falla, el mapa se genera sin esa capa.
