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
from .utils import cargar_config, ciclo_tag, log, ruta_data, ruta_outputs

# Lado mayor de los PNG embebidos en el HTML. Por defecto alcanza para que
# ambas regiones se dibujen a resolución nativa del DEM (Coquimbo 4454,
# Atacama 5690), de modo que los bloques del overlay midan lo mismo que las
# celdas de las capas vectoriales (zonas nuevas) y no se vean más gruesos.
# Subirlo casi no pesa —el HTML lo dominan los GeoJSON, y el PNG comprime
# muy bien las zonas planas— pero sí alarga la generación. Se puede bajar
# por región con `mapa.max_pixeles_overlay` en el config.
MAX_PIXELES_OVERLAY = 6000

# Parámetros de la capa de profundidad, compartidos por el overlay y su
# leyenda: si divergen, la barra de color miente sobre lo que se ve.
CAPA_PROFUNDIDAD = "Anegamiento proyectado (profundidad m)"
PROFUNDIDAD_CMAP = "YlGnBu"
PROFUNDIDAD_VMAX = 3.0
PROFUNDIDAD_UMBRAL = 0.05   # = flood_model.PROFUNDIDAD_MIN_M
# La opacidad máxima se mantiene en el 0.65 que tenía la capa plana: así
# ninguna celda queda más tapada que antes y el cambio solo agrega
# transparencia (las láminas delgadas bajan de 0.65 a ~0.23).
PROFUNDIDAD_ALFA_MIN = 0.22
PROFUNDIDAD_OPACIDAD = 0.65

# La capa de lluvia NO se autoescala: usa clases fijas en mm, iguales en todo
# mapa. Con vmax autoescalado al máximo regional, dos regiones del mismo ciclo
# se dibujaban con el mismo azul saturado teniendo acumulados muy distintos
# (Atacama 11 mm y Coquimbo 62 mm el 29-07-2026, a cada lado del límite), y un
# escenario de 200 mm se veía igual que un GFS de 15 mm en el carrusel.
#
# Compartir un vmax por ciclo no alcanzaba: el castigo es doble porque el mismo
# valor normalizado elige el color Y el alfa, así que la región seca queda
# pálida y transparente a la vez (Atacama 15.6 mm sobre vmax 42.2 → alfa 0.26,
# y 0.13 con el ciclo de las 06). Las clases desacoplan ambas cosas: cada banda
# tiene color y alfa propios, así 3 mm se ven aunque en otra región llueva 300.
#
# Van como constante de módulo y no en config.yaml a propósito: el sentido de
# la escala es ser idéntica entre regiones, y un parámetro por región la
# rompería. Es la misma razón por la que PROFUNDIDAD_VMAX tampoco es de config.
CAPA_PRECIPITACION = "Precipitación pronosticada (mm)"
PRECIPITACION_CMAP = "BuPu"
# cortes en mm/72 h: cada valor es el piso de su clase; la última es abierta.
# Cubren el rango observado (GFS 15.6 → IFS 315.9) sin que la región seca
# caiga toda en la primera banda. Bajo el primer corte no se pinta nada.
PRECIPITACION_CLASES = [1, 5, 15, 30, 60, 120]
# el alfa arranca alto para que la banda más baja se lea sobre el satelital;
# sube poco, para que los núcleos resalten sin tapar el fondo
PRECIPITACION_ALFA_MIN = 0.40
PRECIPITACION_OPACIDAD = 0.75
# la rampa BuPu arranca casi blanca: se muestrea desde 0.25 para que la clase
# más baja tenga color propio en vez de ser un tinte
PRECIPITACION_CMAP_DESDE = 0.25


def _leer_reducido(ruta: Path, max_px: int = MAX_PIXELES_OVERLAY
                   ) -> tuple[np.ndarray, list]:
    """Lee un raster reducido a `max_px` de lado mayor y sus bounds [[s,o],[n,e]].

    Si el ráster ya cabe en `max_px` no se remuestrea (escala < 1 se acota a 1),
    así que basta con que `max_px` supere el lado mayor del DEM para dibujar a
    resolución nativa.
    """
    with rasterio.open(ruta) as src:
        escala = max(src.height, src.width) / max_px
        alto = max(1, int(src.height / max(escala, 1)))
        ancho = max(1, int(src.width / max(escala, 1)))
        datos = src.read(1, out_shape=(alto, ancho), resampling=Resampling.average)
        if src.nodata is not None:
            datos = np.where(datos == src.nodata, np.nan, datos)
        b = src.bounds
    return datos, [[b.bottom, b.left], [b.top, b.right]]


def _overlay(mapa, ruta, nombre, cmap, vmax=None, opacidad=0.65, mostrar=True,
             umbral=0.0, gradual=False, alfa_min=0.0,
             max_px=MAX_PIXELES_OVERLAY, clases=None):
    datos, bounds = _leer_reducido(ruta, max_px)
    vmax = vmax or float(np.nanmax(datos)) or 1.0
    valores = np.nan_to_num(datos)
    if clases:
        # escala discreta: el valor cae en una banda y toma su color y alfa,
        # sin depender del máximo del ráster. `digitize` devuelve 0 para lo que
        # está bajo el primer corte, que queda transparente.
        colores, alfas = _estilo_clases(cmap, len(clases), alfa_min, opacidad)
        idx = np.digitize(valores, clases) - 1
        rgba = np.zeros(valores.shape + (4,))
        for i, ((r, g, b, _), a) in enumerate(zip(colores, alfas, strict=True)):
            rgba[idx == i] = (r, g, b, a)
        folium.raster_layers.ImageOverlay(
            image=rgba, bounds=bounds, name=nombre, show=mostrar, zindex=2,
            mercator_project=True,
        ).add_to(mapa)
        return vmax
    norm = matplotlib.colors.Normalize(vmin=0, vmax=vmax)
    rgba = matplotlib.colormaps[cmap](norm(valores))
    if gradual:
        # transparencia proporcional a la intensidad: no tapa el fondo.
        # alfa_min es el piso para el valor más bajo sobre el umbral: sin él
        # las celdas bajas de un colormap que arranca casi blanco (YlGnBu)
        # se pintan opacas y borran el satelital en vez de teñirlo.
        intensidad = np.clip(norm(valores), 0.0, 1.0)
        rgba[..., 3] = alfa_min + (opacidad - alfa_min) * intensidad
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


def _estilo_clases(cmap: str = PRECIPITACION_CMAP,
                   n: int = len(PRECIPITACION_CLASES),
                   alfa_min: float = PRECIPITACION_ALFA_MIN,
                   opacidad: float = PRECIPITACION_OPACIDAD,
                   desde: float = PRECIPITACION_CMAP_DESDE
                   ) -> tuple[list[tuple], list[float]]:
    """Color y alfa de cada clase: la usan el ráster y la leyenda.

    Es el punto único donde se decide cómo se ve una clase; si el overlay y la
    barra los calcularan por separado, la leyenda podría mentir sobre el mapa.
    """
    colores, alfas = [], []
    for i in range(n):
        t = i / (n - 1)
        colores.append(matplotlib.colormaps[cmap](desde + (1 - desde) * t))
        alfas.append(alfa_min + (opacidad - alfa_min) * t)
    return colores, alfas


def _paradas_clases(colores: list[tuple], alfas: list[float]) -> str:
    """Degradado CSS escalonado: cada clase un bloque macizo de igual ancho.

    Sin los cortes duros el ojo interpola entre clases y la barra deja de
    describir una escala discreta.
    """
    n = len(colores)
    tramos = []
    for i, ((r, g, b, _), a) in enumerate(zip(colores, alfas, strict=True)):
        ini, fin = 100 * i / n, 100 * (i + 1) / n
        css = f"rgba({round(r * 255)},{round(g * 255)},{round(b * 255)},{a:.2f})"
        tramos.append(f"{css} {ini:.4f}% {fin:.4f}%")
    return ", ".join(tramos)


def _paradas_gradiente(cmap: str, alfa_min: float, opacidad: float,
                       n: int = 11) -> list[str]:
    """Paradas CSS del degradado, muestreadas del colormap de la capa.

    Se reproduce también el alfa del overlay (piso `alfa_min` → `opacidad`),
    de modo que la barra se vea como se ve el ráster sobre el mapa base y no
    como el colormap puro, que es bastante más saturado.
    """
    paradas = []
    for i in range(n):
        t = i / (n - 1)
        r, g, b, _ = matplotlib.colormaps[cmap](t)
        alfa = alfa_min + (opacidad - alfa_min) * t
        paradas.append(f"rgba({round(r * 255)},{round(g * 255)},{round(b * 255)},"
                       f"{alfa:.2f}) {round(t * 100)}%")
    return paradas


def _fmt_mm(v: float) -> str:
    """Milímetros para las marcas de la barra: sin decimales desde 10 mm.

    El vmax de lluvia sale de `np.nanmax`, así que trae ruido de punto flotante
    ('62.24000549316406'); redondear a un ancho fijo mantiene las tres marcas
    legibles en la barra angosta del móvil.
    """
    return f"{v:.0f}" if abs(v) >= 10 else f"{v:.1f}"


def _leyenda(id_html: str, titulo: str, gradiente: str, marcas: list[str],
             modo: str = "extremos", mostrar: bool = True,
             nota: str = "") -> str:
    """Barra de color flotante de una capa ráster.

    Dos modos, según cómo se rotula la barra:

    - `extremos`: tres marcas (piso / medio / techo) para una rampa continua.
      Cada capa decide el texto — profundidad clampea en su vmax y escribe
      '≥3'.
    - `clases`: una marca por banda, alineada al borde izquierdo de su bloque,
      porque en una escala discreta el número es el piso de la clase y no un
      punto de la rampa.

    El fondo va a cuadros claros porque la rampa es semitransparente y sobre
    blanco puro los valores bajos no se distinguen.
    """
    oculto = "" if mostrar else "display:none;"
    attr_nota = f" title='{nota}'" if nota else ""
    spans = "".join(f"<span>{m}</span>" for m in marcas)
    return f"""
<div class='leyenda-mapa' id='{id_html}' style='{oculto}'>
  <div class='leyenda-titulo'>{titulo}</div>
  <div class='leyenda-barra'{attr_nota}
       style='background-image:linear-gradient(to right, {gradiente}),
         repeating-conic-gradient(#fff 0% 25%, #d8d8d8 0% 50%)'></div>
  <div class='leyenda-marcas leyenda-marcas--{modo}'>{spans}</div>
</div>
"""


def _estilos_leyendas() -> str:
    """CSS común de las leyendas: contenedor apilado + look de cada barra.

    El contenedor es flex `column-reverse` anclado abajo a la izquierda: al
    ocultar una leyenda su caja colapsa y la otra baja sola al ancla, así las
    dos capas se pueden prender y apagar en cualquier orden sin que queden
    huecos ni superposiciones. El `bottom` deja libre el control de escala de
    Leaflet, que con sus dos filas (km y mi) sube hasta 35px del borde: con
    los 32px que había antes la fila de marcas quedaba tapada.
    """
    return """
<style>
#leyendas-mapa {
  /* barra y marcas comparten el ancho: si se escriben por separado, la fila
     de marcas la termina estirando el título (más largo que la barra) y la
     marca derecha queda pasada del extremo del degradado */
  --ancho-barra: 160px;
  position: fixed; bottom: 40px; left: 10px; z-index: 1000;
  display: flex; flex-direction: column-reverse; gap: 6px;
  font-family: sans-serif; font-size: 12px;
}
.leyenda-mapa {
  background: rgba(255,255,255,.92); padding: 6px 10px; border-radius: 6px;
  box-shadow: 0 1px 4px rgba(0,0,0,.3);
}
.leyenda-titulo { font-weight: bold; margin-bottom: 4px; }
.leyenda-barra {
  width: var(--ancho-barra); height: 12px; border: 1px solid #999;
  border-radius: 2px;
  background-color: #e8e8e8; background-size: 100% 100%, 8px 8px;
}
.leyenda-marcas {
  display: flex; width: var(--ancho-barra); color: #444; margin-top: 2px;
}
/* celdas iguales en vez de space-between: con etiquetas de ancho distinto
   ('1.0' vs '16') space-between descentra las del medio respecto de la barra */
.leyenda-marcas span { flex: 1 1 0; }
/* rampa continua: piso, medio y techo, con los extremos pegados a los bordes
   para no desbordar la barra */
.leyenda-marcas--extremos span:first-child { text-align: left; }
.leyenda-marcas--extremos span:nth-child(2) { text-align: center; }
.leyenda-marcas--extremos span:last-child { text-align: right; }
/* escala discreta: cada número es el piso de su clase, así que va alineado al
   borde izquierdo del bloque que rotula */
.leyenda-marcas--clases { font-size: 0.85em; }
.leyenda-marcas--clases span { text-align: left; }
</style>
"""


def generar_mapa(cfg: dict, sufijo: str | None = None) -> Path:
    """Mapa HTML de la corrida `sufijo` (gfs, ifs o nombre de escenario;
    sin sufijo, pronostico.modelo). Etiqueta, capa de lluvia y rasters de
    resultado provienen todos del mismo sufijo — no se mezclan fuentes."""
    sufijo = sufijo or cfg["pronostico"]["modelo"]
    meta = json.loads(ingest_forecast.ruta_meta(cfg, sufijo).read_text())
    o, s, e, n = cfg["region"]["bbox"]
    cfg_mapa = cfg.get("mapa") or {}
    vista = cfg_mapa.get("vista_inicial") or {}
    centro = vista.get("centro", [(s + n) / 2, (o + e) / 2])
    zoom = vista.get("zoom", 8)
    max_px = int(cfg_mapa.get("max_pixeles_overlay", MAX_PIXELES_OVERLAY))
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

    # precipitación en clases fijas: el color de una celda depende solo de sus
    # mm, no del máximo de la región ni del ciclo
    _overlay(mapa, ingest_forecast.ruta_precip(cfg, sufijo),
             CAPA_PRECIPITACION, PRECIPITACION_CMAP,
             clases=PRECIPITACION_CLASES, alfa_min=PRECIPITACION_ALFA_MIN,
             opacidad=PRECIPITACION_OPACIDAD, mostrar=False, max_px=max_px)
    # profundidad proyectada: alfa gradual con piso, para que las láminas
    # delgadas (la mayoría de las celdas) tiñan el fondo en vez de taparlo
    _overlay(mapa, ruta_outputs(cfg, f"profundidad_{sufijo}.tif"),
             CAPA_PROFUNDIDAD, PROFUNDIDAD_CMAP, vmax=PROFUNDIDAD_VMAX,
             umbral=PROFUNDIDAD_UMBRAL, gradual=True,
             alfa_min=PROFUNDIDAD_ALFA_MIN, opacidad=PROFUNDIDAD_OPACIDAD,
             max_px=max_px)
    # huella histórica
    ruta_union = ruta_data(cfg, "historical", "huella_historica_union.tif")
    if ruta_union.exists():
        _overlay(mapa, ruta_union, "Huella histórica observada (2015/2017)",
                 "Greys", vmax=1.0, opacidad=0.5, mostrar=False, umbral=0.5,
                 max_px=max_px)

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
    tag = ciclo_tag(meta.get("ciclo"))
    if meta.get("ciclo"):
        ciclo_dt = datetime.fromisoformat(meta["ciclo"])
        # máx, media y mediana juntas: el máximo es una sola celda (la más
        # lluviosa de la región) y suele ser varias veces la media, así que
        # por sí solo da una idea equivocada de cuánta lluvia recibe el
        # territorio; la mediana separa un frente que moja todo (mediana ≈
        # media) de un núcleo localizado (mediana ≪ media). La mediana se
        # agregó después, así que se muestra solo si el meta la trae: los
        # ciclos descargados antes deben seguir renderizando igual.
        acum = f"máx {meta['precip_max_mm']} / media {meta['precip_media_mm']}"
        if meta.get("precip_mediana_mm") is not None:
            acum += f" / mediana {meta['precip_mediana_mm']}"
        fuente_txt = (f"Fuente lluvia: {meta['fuente'].upper()} "
                      f"{ciclo_dt:%d-%m-%Y} (ciclo {ciclo_dt:%H} UTC) | "
                      f"acumulado {acum} mm en {meta['horas']} h")
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

    # Leyendas: profundidad primero en el DOM para que el `column-reverse` la
    # deje abajo (es la capa encendida por default, conviene que su posición
    # no se mueva cuando se prende la de lluvia, que aparece encima).
    leyenda_profundidad = _leyenda(
        "leyenda-profundidad", "Profundidad anegamiento (m)",
        ", ".join(_paradas_gradiente(PROFUNDIDAD_CMAP, PROFUNDIDAD_ALFA_MIN,
                                     PROFUNDIDAD_OPACIDAD)),
        [f"{PROFUNDIDAD_UMBRAL:g}", f"{PROFUNDIDAD_VMAX / 2:g}",
         f"&ge;{PROFUNDIDAD_VMAX:g}"])
    # la de lluvia arranca oculta: su capa se agrega con mostrar=False
    colores, alfas = _estilo_clases()
    leyenda_precip = _leyenda(
        "leyenda-precipitacion", f"Precipitación ({meta['horas']} h, mm)",
        _paradas_clases(colores, alfas),
        # los cortes son enteros: '5' y no '5.0', que además ocupa menos en la
        # fila de 6 marcas
        [f"{c:g}" for c in PRECIPITACION_CLASES], modo="clases",
        mostrar=False,
        nota="Escala fija: los colores significan los mismos mm en toda "
             "región, ciclo y escenario.")
    mapa.get_root().html.add_child(folium.Element(_estilos_leyendas()))
    mapa.get_root().html.add_child(folium.Element(
        f"<div id='leyendas-mapa'>{leyenda_profundidad}{leyenda_precip}</div>"))
    folium.LayerControl(collapsed=False).add_to(mapa)

    # Cada leyenda solo tiene sentido con su capa encendida. Leaflet emite
    # overlayadd/overlayremove con el nombre de la capa al togglearla desde
    # el LayerControl; el nombre del objeto mapa lo pone folium al generar.
    por_capa = {CAPA_PROFUNDIDAD: "leyenda-profundidad",
                CAPA_PRECIPITACION: "leyenda-precipitacion"}
    sync_leyenda = f"""
<script>
document.addEventListener('DOMContentLoaded', function () {{
  if (typeof {mapa.get_name()} === 'undefined') return;
  var porCapa = {json.dumps(por_capa, ensure_ascii=False)};
  function alternar(visible) {{
    return function (e) {{
      var id = porCapa[e.name];
      var leyenda = id && document.getElementById(id);
      if (leyenda) leyenda.style.display = visible ? 'block' : 'none';
    }};
  }}
  {mapa.get_name()}.on('overlayadd', alternar(true));
  {mapa.get_name()}.on('overlayremove', alternar(false));
}});
</script>
"""
    mapa.get_root().html.add_child(folium.Element(sync_leyenda))

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
  /* las dos leyendas pueden estar visibles a la vez: se achican y se juntan
     para que apiladas sigan cabiendo sobre el borde inferior del mapa */
  #leyendas-mapa {
    font-size: 0.72em !important;
    /* el control de escala no se achica con el media query: mismos 35px que
       en desktop, así que el ancla tampoco puede bajar de ahí */
    bottom: 38px !important;
    gap: 4px !important;
    --ancho-barra: 105px;   /* barra y marcas lo toman de acá */
  }
  .leyenda-mapa { padding: 4px 7px !important; }
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
        cfg, f"mapa_anegamientos_{sufijo}{tag}_{generado:%Y%m%d-%H%M%S}.html")
    mapa.save(str(destino))
    log.info("Mapa guardado: %s", destino)
    return destino


if __name__ == "__main__":
    cfg = cargar_config()
    print(generar_mapa(cfg))
