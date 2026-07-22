"""Mapa interactivo folium con las capas del análisis.

Capas: precipitación pronosticada, profundidad proyectada, huellas históricas,
zonas nuevas (rojo), vías y servicios expuestos.
"""

import json
from datetime import datetime
from pathlib import Path

import folium
import geopandas as gpd
import matplotlib
import numpy as np
import rasterio
from rasterio.warp import Resampling

from . import ingest_forecast
from .utils import cargar_config, log, ruta_data, ruta_outputs

MAX_PIXELES_OVERLAY = 1600  # lado mayor de los PNG embebidos en el HTML


def _leer_reducido(ruta: Path) -> tuple[np.ndarray, list]:
    """Lee un raster reducido a tamaño de overlay y sus bounds [[s,o],[n,e]]."""
    with rasterio.open(ruta) as src:
        escala = max(src.height, src.width) / MAX_PIXELES_OVERLAY
        alto = max(1, int(src.height / max(escala, 1)))
        ancho = max(1, int(src.width / max(escala, 1)))
        datos = src.read(1, out_shape=(alto, ancho), resampling=Resampling.average)
        if src.nodata is not None:
            datos = np.where(datos == src.nodata, np.nan, datos)
        b = src.bounds
    return datos, [[b.bottom, b.left], [b.top, b.right]]


def _overlay(mapa, ruta, nombre, cmap, vmax=None, opacidad=0.65, mostrar=True,
             umbral=0.0, gradual=False):
    datos, bounds = _leer_reducido(ruta)
    vmax = vmax or float(np.nanmax(datos)) or 1.0
    norm = matplotlib.colors.Normalize(vmin=0, vmax=vmax)
    valores = np.nan_to_num(datos)
    rgba = matplotlib.colormaps[cmap](norm(valores))
    if gradual:
        # transparencia proporcional a la intensidad: no tapa el fondo
        rgba[..., 3] = np.clip(norm(valores), 0.0, 1.0) * opacidad
        rgba[valores <= umbral, 3] = 0.0
    else:
        rgba[..., 3] = np.where(valores > umbral, opacidad, 0.0)
    folium.raster_layers.ImageOverlay(
        # mercator_project: el raster viene en grilla geográfica regular
        # (EPSG:4326) y sin esto Leaflet lo estira lineal en Mercator,
        # desplazando el interior ~2.5 km al sur a la latitud de la región
        image=rgba, bounds=bounds, name=nombre, show=mostrar, zindex=2,
        mercator_project=True,
    ).add_to(mapa)
    return vmax


def generar_mapa(cfg: dict, sufijo: str | None = None) -> Path:
    """Mapa HTML de la corrida `sufijo` (gfs, ifs o nombre de escenario;
    sin sufijo, pronostico.modelo). Etiqueta, capa de lluvia y rasters de
    resultado provienen todos del mismo sufijo — no se mezclan fuentes."""
    sufijo = sufijo or cfg["pronostico"]["modelo"]
    meta = json.loads(ingest_forecast.ruta_meta(cfg, sufijo).read_text())
    o, s, e, n = cfg["region"]["bbox"]
    vista = cfg.get("mapa", {}).get("vista_inicial") or {}
    centro = vista.get("centro", [(s + n) / 2, (o + e) / 2])
    zoom = vista.get("zoom", 8)
    mapa = folium.Map(location=centro, zoom_start=zoom,
                      tiles=None, control_scale=True)

    # bases: satelital (Esri World Imagery, gratuita con atribución) por
    # defecto; el fondo claro queda como alternativa en el LayerControl
    folium.TileLayer(
        tiles=("https://server.arcgisonline.com/ArcGIS/rest/services/"
               "World_Imagery/MapServer/tile/{z}/{y}/{x}"),
        attr="Esri, Maxar, Earthstar Geographics",
        name="Satélite (Esri)", show=True,
    ).add_to(mapa)
    folium.TileLayer("cartodbpositron", name="Fondo claro (Carto)",
                     show=False).add_to(mapa)

    # precipitación (alfa gradual: los núcleos intensos resaltan, el resto
    # deja ver el mapa base)
    _overlay(mapa, ingest_forecast.ruta_precip(cfg, sufijo),
             "Precipitación pronosticada (mm)", "Blues", opacidad=0.7,
             mostrar=False, umbral=1.0, gradual=True)
    # profundidad proyectada
    _overlay(mapa, ruta_outputs(cfg, f"profundidad_{sufijo}.tif"),
             "Anegamiento proyectado (profundidad m)", "YlGnBu", vmax=3.0,
             umbral=0.05)
    # huella histórica
    ruta_union = ruta_data(cfg, "historical", "huella_historica_union.tif")
    if ruta_union.exists():
        _overlay(mapa, ruta_union, "Huella histórica observada (2015/2017)",
                 "Greys", vmax=1.0, opacidad=0.5, mostrar=False, umbral=0.5)

    # zonas nuevas (lo central del análisis) en rojo
    ruta_nuevas = ruta_outputs(cfg, f"zonas_nuevas_{sufijo}.geojson")
    if ruta_nuevas.exists():
        nuevas = gpd.read_file(ruta_nuevas)
        if not nuevas.empty:
            folium.GeoJson(
                nuevas.__geo_interface__, name="ZONAS NUEVAS de anegamiento",
                style_function=lambda _: {"color": "#c0392b", "weight": 1,
                                          "fillColor": "#e74c3c",
                                          "fillOpacity": 0.55},
                tooltip="Zona nueva: sin registro de inundación histórica",
            ).add_to(mapa)

    # exposición
    ruta_exp = ruta_outputs(cfg, f"exposicion_{sufijo}.json")
    if ruta_exp.exists():
        exp = json.loads(ruta_exp.read_text())
        capa_serv = folium.FeatureGroup(name="Servicios críticos expuestos")
        for sv in exp.get("servicios", []):
            folium.Marker(
                [sv["lat"], sv["lon"]],
                popup=f"{sv['tipo']}: {sv['nombre']}",
                icon=folium.Icon(color="red", icon="warning-sign"),
            ).add_to(capa_serv)
        capa_serv.add_to(mapa)
        ruta_vias = ruta_outputs(cfg, f"vias_expuestas_{sufijo}.geojson")
        if ruta_vias.exists():
            vias = gpd.read_file(ruta_vias)
            if not vias.empty:
                folium.GeoJson(vias.__geo_interface__, name="Vías expuestas",
                               style_function=lambda _: {"color": "#e67e22",
                                                         "weight": 2},
                               show=False).add_to(mapa)

    generado = datetime.now()
    ciclo_tag = ""
    if meta.get("ciclo"):
        ciclo_dt = datetime.fromisoformat(meta["ciclo"])
        ciclo_tag = f"_{ciclo_dt:%Y%m%d}_{ciclo_dt:%H}utc"
        fuente_txt = (f"Fuente lluvia: {meta['fuente'].upper()} "
                      f"{ciclo_dt:%d-%m-%Y} (ciclo {ciclo_dt:%H} UTC) | acumulado máx "
                      f"{meta['precip_max_mm']} mm / {meta['horas']} h")
    else:
        fuente_txt = (f"Escenario sintético: {meta['precip_max_mm']} mm / "
                      f"{meta['horas']} h uniformes (no depende de ciclo GFS)")

    titulo = (f"<div id='titulo-mapa' style='position:fixed;top:10px;left:60px;"
              f"z-index:1000;"
              f"background:rgba(255,255,255,.92);padding:8px 14px;border-radius:6px;"
              f"box-shadow:0 1px 4px rgba(0,0,0,.3);font-family:sans-serif'>"
              f"<b>Proyección de anegamientos — {cfg['region']['nombre']}</b>"
              f"<span id='titulo-flecha'> &#9660;</span>"
              f"<span id='titulo-detalle'><br>"
              f"{fuente_txt} | isoterma 0: {meta['isoterma0_m']} m<br>"
              f"<span style='color:#666;font-size:0.85em'>generado: "
              f"{generado:%d-%m-%Y %H:%M:%S}</span></span></div>")
    mapa.get_root().html.add_child(folium.Element(titulo))
    folium.LayerControl(collapsed=False).add_to(mapa)

    # En pantallas angostas (smartphone) la tarjeta de título y el selector de
    # capas expandido tapan el mapa. Con collapsed=False Leaflet no instala los
    # listeners de abrir/cerrar, así que se agrega un toggle propio: alternar la
    # clase leaflet-control-layers-expanded reproduce el expandir/colapsar
    # nativo. En desktop nada cambia (el media query no aplica).
    responsive = """
<style>
#titulo-flecha { display: none; }
@media (max-width: 767px) {
  #titulo-mapa {
    font-size: 0.72em !important;
    max-width: 60vw;
    padding: 5px 8px !important;
  }
  /* colapsada por defecto: solo el título; tap despliega los detalles */
  #titulo-flecha { display: inline; font-size: 0.8em; color: #666; }
  #titulo-mapa:not(.expandido) #titulo-detalle { display: none; }
  #titulo-mapa.expandido #titulo-flecha { display: none; }
}
</style>
<script>
document.addEventListener('DOMContentLoaded', function () {
  if (!window.matchMedia('(max-width: 767px)').matches) return;
  var tit = document.getElementById('titulo-mapa');
  tit.addEventListener('click', function () {
    tit.classList.toggle('expandido');
  });
  var ctl = document.querySelector('.leaflet-control-layers');
  if (!ctl) return;
  ctl.classList.remove('leaflet-control-layers-expanded');
  ctl.querySelector('.leaflet-control-layers-toggle')
     .addEventListener('click', function (e) {
        e.preventDefault();
        ctl.classList.add('leaflet-control-layers-expanded');
     });
  // tocar fuera del control lo vuelve a colapsar (los clicks dentro del
  // control no llegan aquí: Leaflet les corta la propagación)
  document.addEventListener('click', function (e) {
    if (!ctl.contains(e.target)) {
      ctl.classList.remove('leaflet-control-layers-expanded');
    }
  });
});
</script>
"""
    mapa.get_root().html.add_child(folium.Element(responsive))

    destino = ruta_outputs(
        cfg, f"mapa_anegamientos_{sufijo}{ciclo_tag}_{generado:%Y%m%d-%H%M%S}.html")
    mapa.save(str(destino))
    log.info("Mapa guardado: %s", destino)
    return destino


if __name__ == "__main__":
    cfg = cargar_config()
    print(generar_mapa(cfg))
