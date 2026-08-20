#!/usr/bin/env bash
# One-shot (cron del 22-ago-2026): regenera la huella Sentinel-1 del evento
# agosto_2026 de Antofagasta cuando ya existan pasadas post-evento (la primera
# fue S1C 19-ago 23z; al 22-ago debería haber 4-5), re-deriva marzo_2015 con
# el filtro de salares operativo y calibra con guardia anti-saturación.
# Reutilizable a mano si la cobertura del 22-ago resulta insuficiente.
# Requiere refresh token openeo vigente (~/.local/share/openeo-python-client/).
set -euo pipefail

REPO="/home/mherrera/Proyectos/meteorologia/meteorologia-flood-projections"
HIST="$REPO/data/antofagasta/historical"
LOGDIR="$REPO/outputs/antofagasta/logs"
LOGFILE="$LOGDIR/regenerar_huella_$(date -u +%Y%m%dT%H%MZ).log"
mkdir -p "$LOGDIR"
cd "$REPO"
exec >>"$LOGFILE" 2>&1
echo "== inicio $(date -u -Iseconds) =="

# 1. Borrar la caché por existencia del evento agosto_2026 para forzar la
#    re-descarga con las pasadas nuevas (sin esto, reutilizaría el mosaico
#    vacío del 19-ago). Se conserva s1_min_vv_marzo_2015.tif (69 MB ya
#    descargados); su huella se re-deriva más abajo.
rm -rf "$HIST/s1_teselas_agosto_2026"
rm -f "$HIST/s1_min_vv_agosto_2026.tif" \
      "$HIST/huella_agosto_2026.tif" "$HIST/huella_agosto_2026.tif.pendiente" \
      "$HIST/huella_historica_union.tif"

# 2. Primera pasada: descarga el backscatter de agosto_2026 y deriva su
#    máscara. La de marzo_2015 que exista hasta aquí quedó derivada SIN el
#    filtro de salares (cuando agosto no tenía datos), así que...
PYTHONPATH=src "$REPO/.venv/bin/python" - <<'PY'
from inundaciones.utils import cargar_config
from inundaciones import ingest_sentinel1
cfg = cargar_config('config_antofagasta.yaml')
print(ingest_sentinel1.preparar_mascaras_s1(cfg))
PY

# 3. ...segunda pasada: con ambos s1_min_vv en disco, borrar las huellas y
#    re-derivarlas — recién ahora filtrar_oscuro_permanente puede marcar los
#    salares (necesita >=2 eventos con dato). Sin re-descarga: derivar es
#    barato porque parte de los mosaicos ya bajados.
rm -f "$HIST/huella_marzo_2015.tif" "$HIST/huella_agosto_2026.tif"
PYTHONPATH=src "$REPO/.venv/bin/python" - <<'PY'
from inundaciones.utils import cargar_config
from inundaciones import ingest_sentinel1, ingest_historical
cfg = cargar_config('config_antofagasta.yaml')
print(ingest_sentinel1.preparar_mascaras_s1(cfg))
print(ingest_historical.construir_union(cfg))
PY

# 4. Calibrar y aplicar la guardia anti-saturación: mediana en factor_max
#    (8.0) = la calibración persigue salares u observación espuria — se
#    aparta y el pipeline sigue con factor 1.0 (mismo criterio del 19-ago).
"$REPO/.venv/bin/python" scripts/03_calibrar.py --config config_antofagasta.yaml
MEDIANA=$(grep -o "factor mediano [0-9.]*" "$LOGFILE" | tail -1 | awk '{print $3}')
echo "mediana de factores: ${MEDIANA:-desconocida}"
if [ -n "${MEDIANA:-}" ] && awk "BEGIN{exit !($MEDIANA >= 7.99)}"; then
  mv "$REPO/data/antofagasta/calibracion.json" \
     "$REPO/data/antofagasta/calibracion.json.saturada-$(date -u +%Y%m%d)"
  echo "AVISO: calibración saturada en factor_max — apartada; sigue factor 1.0."
  echo "Revisar cobertura de agosto_2026 en este log y reintentar más adelante."
else
  echo "Calibración aceptada. Las corridas del cron la usan desde la próxima."
  echo "Sugerencia: re-backfillear los 7 ciclos GFS del evento (16-18 ago) con"
  echo "reprocesar_ciclo_gfs.py (NOMADS retiene ~10 días: sirve hasta ~26-ago;"
  echo "los IFS ya están purgados del open-data) y podar duplicados con"
  echo "05_limpiar_outputs.py --aplicar."
fi
echo "== fin $(date -u -Iseconds) =="
