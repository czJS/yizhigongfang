#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT_DIR"

echo "== Desktop regression: typecheck =="
npm run typecheck

echo
echo "== Desktop regression: logic tests =="
npm run test:logic

echo
echo "== Desktop regression: build =="
npm run build

echo
echo "== Manual smoke checklist =="
if [[ -f "SMOKE_CHECKLIST.md" ]]; then
  echo "See: SMOKE_CHECKLIST.md"
else
  echo "SMOKE_CHECKLIST.md not found"
fi

