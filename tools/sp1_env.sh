#!/usr/bin/env bash
# SP1 plan B: run a command with the INT8 Muse engine environment (the same settings as the SP1 captures).
#   tools/sp1_env.sh pixi run --no-install python3 tools/test_steer_verify.py -v
export SO_PATH="${SO_PATH:-$PWD/libnomos_kernel-muse.so}" WEIGHTS=~/nomos_data/muse-glimmer-flat-nvfp4/ \
  TOK_DIR=~/nomos_data/muse-glimmer-tokenizer CUDA_VISIBLE_DEVICES=0 NOMOS_MODEL=muse \
  NOMOS_MODEL_ID=muse-glimmer NOMOS_WEIGHT_NVFP4=1 NOMOS_W4A4=1 NOMOS_PRECISION_BITS=16 NOMOS_KV_QUANT=1 \
  NOMOS_KV_INT4=0 NOMOS_KV_I4_BLOCK=32 NOMOS_KV_SWA=0 NOMOS_KVSTORE=0 NOMOS_MAX_SEQ=4096 \
  NOMOS_PREFILL_CHUNK=256 NOMOS_BATCHED_PREFILL=1
if curl -sf --max-time 3 localhost:8091/health > /dev/null; then
  echo "the serve answers on :8091; stop it first (pyhost/serve_nomos.sh down, from ~/Projects/nomos-kernel-product)" >&2
  exit 3
fi
exec "$@"
