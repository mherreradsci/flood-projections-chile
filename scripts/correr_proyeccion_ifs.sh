#!/usr/bin/env bash
# Wrapper para cron/systemd: corre 04_proyectar.py --fuente ifs con log por corrida.
# Cron no hereda el entorno de la sesión, así que todo va con rutas absolutas.
set -euo pipefail

REPO="/home/mherrera/Proyectos/meteorologia/meteorologia-flood-projections"
LOGDIR="$REPO/outputs/coquimbo/logs"
mkdir -p "$LOGDIR"

# Candado: si una corrida anterior sigue viva (p. ej. NOMADS lento), no solapar.
exec 9>"$LOGDIR/.proyeccion-ifs.lock"
flock -n 9 || { echo "corrida anterior ifs aún en curso; salgo" >&2; exit 1; }

cd "$REPO"
exec >>"$LOGDIR/proyeccion_ifs_$(date -u +%Y%m%dT%H%MZ).log" 2>&1

echo "== inicio $(date -u -Iseconds) =="
"$REPO/.venv/bin/python" scripts/04_proyectar.py --fuente ifs
echo "== fin $(date -u -Iseconds) =="
