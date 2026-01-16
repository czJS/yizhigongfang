#!/usr/bin/env bash
set -euo pipefail

# Wait for onejob to finish, then run Round1.5 in a separate container.
# Logs are appended to outputs/eval/round15_wait_and_run.log

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
LOG="$ROOT/outputs/eval/round15_wait_and_run.log"
mkdir -p "$(dirname "$LOG")"

echo "[$(date)] round15 watcher started" >> "$LOG"

while true; do
  st="$(cd "$ROOT" && docker compose -p yzh exec -T onejob bash -lc "python - <<'PY'
import json
from pathlib import Path
p=Path('/app/outputs/eval/round1_onejob_progress.json')
obj=json.loads(p.read_text(encoding='utf-8'))
print(obj.get('status',''))
PY" 2>/dev/null | tail -n 1 | tr -d '\r')"
  echo "[$(date)] onejob status=$st" >> "$LOG"

  if [[ "$st" == "all_done" ]]; then
    echo "[$(date)] onejob all_done -> start round1.5" >> "$LOG"
    (cd "$ROOT" && docker compose -p yzh run --rm --no-deps onejob bash -lc \
      "python /app/scripts/run_round15_param_sweep.py \
        --doc /app/docs/质量模式配置项测试流程.md \
        --segments /app/eval/e2e_quality/segments_short3.docker.jsonl \
        --base-config /app/config/quality.yaml \
        --bootstrap-iters 2000 \
        --seed 42 \
        --top-k 15 \
        --min-p 0.8 \
        --min-delta 0.2 \
        --jobs 1" >> "$LOG" 2>&1) || true
    echo "[$(date)] round1.5 finished" >> "$LOG"
    break
  fi

  sleep 60
done


