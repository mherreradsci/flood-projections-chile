# Proyección de anegamientos — Región de Coquimbo

Sistema en Python para estimar **dónde se producirán anegamientos** ante un
evento de precipitación extrema (río atmosférico de julio 2026) y detectar los
**puntos nuevos** sin registro histórico de inundación. 100% herramientas de
código abierto y datos públicos.

![Mapa de proyección de anegamientos sobre Punitaqui: zonas nuevas en rojo y servicios críticos expuestos, sobre imagen satelital](docs/ejemplo_mapa_gfs.jpg)

*Salida real del pipeline (GFS, ciclo 00 UTC del 17-jul-2026) sobre Punitaqui:
zonas nuevas de anegamiento en rojo y servicios críticos expuestos.*

## Método

Dos conceptos base:

- **DEM** (*Digital Elevation Model*, modelo digital de elevación): raster
  donde cada celda (~30 m aquí) guarda la altura del terreno sobre el nivel
  del mar. Es el "mapa en 3D" del que se deriva todo lo demás.
- **HAND** (*Height Above Nearest Drainage*): cuántos metros más arriba está
  cada celda respecto del **cauce al que drena** siguiendo la dirección del
  flujo — no de la altura sobre el mar. Si una crecida sube N metros, se
  anegan las celdas con HAND < N; por eso una terraza baja junto a un río
  puede ser más riesgosa que un cerro costero.

Modelo semi-hidrológico **HAND calibrado**:

1. **Terreno**: DEM Copernicus GLO-30 → direcciones de flujo, acumulación, red
   de drenaje y HAND (Height Above Nearest Drainage) con `pysheds`.
2. **Lluvia efectiva**: precipitación GFS 0.25° (o escenario sintético)
   filtrada por la **isoterma 0** — solo el área bajo la cota de nieve aporta
   escorrentía líquida, el mecanismo dominante en crecidas chilenas.
3. **Escorrentía**: SCS Curve Number (CN desde ESA WorldCover) por subcuenca
   HydroBASINS.
4. **Extensión**: el volumen de escorrentía se distribuye en el espacio HAND
   de cada subcuenca (estilo FwDET) → raster de profundidad.
5. **Calibración**: factores de volumen por subcuenca ajustados contra huellas
   de inundación observadas (Global Flood Database MODIS 250 m para 2002;
   máscaras de agua Sentinel-1 vía openEO para 2015 y 2017).
6. **Zonas nuevas**: extensión proyectada − huellas históricas.

## Uso

```bash
python3 -m venv .venv && .venv/bin/pip install -r requirements.txt
.venv/bin/python scripts/01_descargar_datos.py    # insumos + GFS vigente (--sin-pronostico para omitirlo)
.venv/bin/python scripts/02_preparar_terreno.py   # HAND (lento, se cachea)
.venv/bin/python scripts/03_calibrar.py           # contra eventos históricos
.venv/bin/python scripts/04_proyectar.py --fuente gfs
```

Utilitario: `scripts/ciclo_vigente.py` muestra qué ciclos GFS ya publicaron el
horizonte completo (72 h) en NOAA — el más reciente de ellos es el que usará
`--fuente gfs` — sin descargar datos (solo HEAD a los `.idx` del bucket
`noaa-gfs-bdp-pds`).

### Parámetros de `04_proyectar.py`

| Parámetro | Valores | Defecto | Descripción |
|---|---|---|---|
| `--fuente` | `gfs` \| `ifs` \| `escenario` | `gfs` | Origen de la lluvia: pronóstico GFS 0.25° (NOAA), pronóstico IFS 0.25° (ECMWF open-data) o escenario sintético definido en `config.yaml`. Con `gfs`/`ifs` descarga el ciclo vigente antes de modelar. |
| `--escenario NOMBRE` | un nombre de la sección `escenarios:` de `config.yaml` (hoy: `extremo_200mm`, `moderado_100mm`, `costero_120mm`) | `extremo_200mm` | Escenario sintético a usar; solo tiene efecto con `--fuente escenario`. |
| `--sin-exposicion` | flag (sin valor) | desactivado | Omite la consulta Overpass/OSM de vías y servicios expuestos; el mapa se genera sin esa capa. Si la consulta falla, el script continúa igual con una advertencia. |

Ejemplos:

```bash
.venv/bin/python scripts/04_proyectar.py --fuente ifs
.venv/bin/python scripts/04_proyectar.py --fuente escenario --escenario extremo_200mm
.venv/bin/python scripts/04_proyectar.py --fuente escenario --escenario moderado_100mm --sin-exposicion
```

Resultado principal:
`outputs/mapa_anegamientos_<fuente>[_<AAAAMMDD>_<HH>utc]_<AAAAMMDD-HHMMSS>.html`
(folium, capas conmutables; el tag `_<AAAAMMDD>_<HH>utc` aparece solo con
pronósticos e indica día y ciclo usados, de modo que ordenar por nombre de
archivo ordena por ciclo) más GeoTIFF/GeoJSON en `outputs/`, sufijados por fuente
(`extension_gfs.tif`, `zonas_nuevas_extremo_200mm.geojson`, …).

### Corridas programadas (cron)

`scripts/correr_proyeccion_gfs.sh` es el wrapper para cron/systemd: rutas
absolutas, candado `flock` contra corridas solapadas y log por corrida en
`outputs/logs/proyeccion_<timestamp>.log`.

Las horas de ejecución deben seguir la publicación de GFS: cada ciclo (00, 06,
12, 18 UTC) completa su horizonte de 72 h ~4 h después de la hora del ciclo.
El pipeline sondea NOAA y descarga el ciclo más reciente ya completo (con la
heurística de rezago fijo de 5 h solo como respaldo si el sondeo falla), así
que basta programar las corridas después de ese punto: en Chile continental en
invierno (UTC−4), a las 01:00, 07:00, 13:00 y 19:00 locales — cada una toma el
ciclo recién completado. Ejemplo usado durante el evento de julio 2026
(días 16–21, con guardia de año y entrada de autolimpieza que el último día se
borra a sí misma y a la de corridas):

```cron
0 1,7,13,19 16-21 7 * [ "$(date +\%Y)" = "2026" ] && /home/mherrera/Proyectos/meteorologia/scripts/correr_proyeccion_gfs.sh # proyeccion-gfs-jul2026
30 19 21 7 * crontab -l | grep -v proyeccion-gfs-jul2026 | crontab - # proyeccion-gfs-jul2026
```

## Datos usados (todos públicos)

| Insumo | Fuente |
|---|---|
| DEM 30 m | Copernicus GLO-30 (AWS Open Data) |
| Pronóstico | GFS 0.25° vía `herbie-data` (NOAA) o IFS 0.25° vía `ecmwf-opendata` |
| Huellas históricas | Global Flood Database v1.4 (GCS `gfd_v1_4`) |
| Huellas 2015/2017 | Sentinel-1 GRD vía openEO (Copernicus Dataspace) |
| Uso de suelo | ESA WorldCover 10 m (AWS) |
| Subcuencas | HydroSHEDS HydroBASINS nivel 8 |
| Límite regional | OpenStreetMap (Nominatim) |
| Exposición | OpenStreetMap (Overpass vía `osmnx`) |

## Limitaciones

- GFS 25 km es grueso para quebradas costeras; usar el modo escenario para
  forzar acumulados locales.
- La calibración usa tres eventos: agosto de 2002 (DFO 2042, la **única**
  huella MODIS sobre Coquimbo en el Global Flood Database — los aluviones de
  2015 y 2017 no fueron procesados por GFD v1.4) más marzo 2015 y mayo 2017
  con máscaras de agua Sentinel-1 (openEO/Copernicus Dataspace). Las huellas
  satelitales subdetectan agua somera o breve, por lo que el modelo se calibra
  al corredor que captura el 80% de la observación (POD≈0.8) y **tiende a
  sobrepredecir extensión** — es un producto de susceptibilidad, no un mapa de
  certeza.
- El modelo representa anegamiento fluvial/de quebradas, no fallas de
  colectores urbanos.
- **Esto no reemplaza los avisos oficiales de la DMC ni de SENAPRED.**
