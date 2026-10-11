#!/bin/bash
# measure-8b.sh — does the 8B tier actually beat the 1.7B on this machine?
#
# Runs a SECOND llama-server on 8791 with the 8B, leaving the production server
# on 8765 untouched, then scores the same 62 eval cases that produced the
# recorded 1.7B numbers (56/62 tool+args at 7.7s mean, `select` config).
#
# The question is not "does 8B exist" but "is it better where it counts, at a
# price worth paying" — the repo's own history chose `select` over
# `select+recover` at equal accuracy and 5x the speed, so accuracy alone is not
# the answer.
set -u
PORT=${PORT:-8791}
MODEL=${MODEL:-$HOME/.local/share/shani-chronoa/llm/Qwen3-8B-Q4_K_M.gguf}
HERE=$(cd "$(dirname "$0")" && pwd)
CHRONOA=${CHRONOA:-$HERE/..}

[ -f "$MODEL" ] || { echo "no model at $MODEL"; exit 1; }

SRV=$(mktemp -d /tmp/opencode/m8b-XXXX)
mkdir -p "$SRV"

echo "=== starting llama-server with the 8B on port $PORT ==="
/usr/bin/llama-server -m "$MODEL" --host 127.0.0.1 --port "$PORT" --jinja -c 8192 \
  -ngl 0 -t "$(($(nproc) - 1))" --no-webui >"$SRV/server.log" 2>&1 &
SRV_PID=$!
trap 'kill $SRV_PID 2>/dev/null' EXIT

for _ in $(seq 1 60); do
  curl -s --max-time 2 "http://127.0.0.1:$PORT/health" >/dev/null 2>&1 && break
  sleep 2
done
curl -s --max-time 3 "http://127.0.0.1:$PORT/health" >/dev/null 2>&1 || {
  echo "server did not come up; log:"; tail -20 "$SRV/server.log"; exit 1; }
echo "up (pid $SRV_PID)"

echo
echo "=== raw decode speed ==="
curl -s --max-time 180 "http://127.0.0.1:$PORT/v1/chat/completions" \
  -H 'Content-Type: application/json' -d '{
    "model":"current.gguf","max_tokens":200,"temperature":0.1,
    "messages":[{"role":"user","content":"Count from 1 to 50, separated by commas."}]}' \
| python3 -c "
import json,sys
d=json.load(sys.stdin); t=d.get('timings',{})
print('  predicted tok/s:', round(t.get('predicted_per_second',0),2))
print('  prompt   tok/s:', round(t.get('prompt_per_second',0),1))"

echo
echo "=== the KV-fit arithmetic, against this model's own header ==="
cd "$CHRONOA" && PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=usr/lib/shani-chronoa \
  CHRONOA_KV_PARITY_MODEL="$MODEL" python3 -m pytest \
  tests/test_ctxfit.py::TestTheTensorRouteAgreesWithTheMetadataRoute -q --tb=line 2>&1 | tail -2
PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=usr/lib/shani-chronoa python3 -c "
from shani_chronoa import ctxfit
import os
info = ctxfit.gguf_model_info(os.path.expanduser('$MODEL'))
if info:
    w = os.path.getsize(os.path.expanduser('$MODEL'))
    pt = ctxfit.kv_bytes_per_token(info)
    print(f'  weights      : {w/1024**3:.2f} GiB')
    print(f'  KV per token : {pt} B  ({pt/1024:.1f} KiB)')
    print(f'  at 8192 ctx  : {(w + 8192*pt)/1024**3:.2f} GiB total need')
    print(f'  at 16384 ctx : {(w + 16384*pt)/1024**3:.2f} GiB total need')
else:
    print('  could not read the header')
"

echo
echo "=== tool-selection eval, the same 62 cases as the recorded 1.7B run ==="
cd "$CHRONOA" && PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=usr/lib/shani-chronoa \
  python3 tools/task_eval.py --port "$PORT" --configs select \
  --markdown "$SRV/eval-8b.md" --json "$SRV/eval-8b.json" 2>&1 | tail -4
echo
echo "--- the 1.7B for comparison (recorded) ---"
grep -E "^\| select " "$CHRONOA/../shani-install-media/test-env/eval-out/eval-qwen3-1.7b.md" 2>/dev/null || echo "  (recorded file not found)"
echo "--- the 8B, measured now ---"
grep -E "^\| select " "$SRV/eval-8b.md" 2>/dev/null
