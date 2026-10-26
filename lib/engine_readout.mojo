"""Readout spine (glimmer-emotions docs/superpowers/specs/2026-10-20-readout-spine-design.md, section 2).

At a configured layer, right after plan B's steering point (post-steer), each computed row's projections onto unit
emotion directions, and its residual L2 norm, go into a per-position table. A position reads valid only if a forward
wrote it since the last begin_turn and it lies below the cache length at read time.

The GPU kernels and their launchers are in lib/readout_kernels.mojo: this module imports GemmaEngine and sits inside
the engine's import cycle, where a kernel compiled here fails to infer its argument types."""

from std.memory import UnsafePointer
from std.math import sqrt
from lib.cuda import cuda_malloc, cuda_free, cuda_memcpy, cuda_sync, cuda_memset_2d, cuda_download_i32
from lib.cublas import cublas_sgemm
from lib.ops_gpu_mojo_reductions import gpu_rmsnorm_batched_mojo
from lib.fp4_gemv_v2 import NVFP4_GEMV_SMAX, gpu_matmul_nvfp4_fused_v3_multirow_dev
from lib.dflash_drafter import DFLASH_MAX_VERIFY_ROWS
from lib.model_config import D, VOCAB, TOTAL_LAYERS, RMS_EPS_FINAL
from lib.gemma4_engine import GemmaEngine
from lib.readout_kernels import (
    readout_launch_proj, readout_launch_mark, readout_launch_copy, readout_launch_topk, readout_launch_mark_kept,
)

comptime RO_STAGE_ROWS = DFLASH_MAX_VERIFY_ROWS   # a DFlash verify stages at most this many rows; a decode step 1


def readout_rows(mut self: GemmaEngine, d_rows: UInt64, S: Int, start_pos: Int, layer: Int, stage: Bool) raises:
    """The hook, right after the steering point of `layer`, for S rows at positions start_pos..
    `stage` is True for a verify call's rows and for a decode step's row: the rows that can get a workspace readout.
    Staging belongs to the last forward only. Layer 0 of every forward resets it, so a commit after an unstaged
    forward finds nothing to commit."""
    if not self.ro_active:
        return
    var n_ws = len(self.ro_ws_layers)
    var staging = stage and n_ws > 0 and S <= RO_STAGE_ROWS
    if layer == 0:
        self.ro_stage_pos = List[Int]()
        if staging:
            for r in range(S):
                self.ro_stage_pos.append(start_pos + r)
    var n_emo = len(self.ro_emo_layers)
    for slot in range(n_emo):
        if self.ro_emo_layers[slot] == layer:
            readout_launch_proj(
                self.ctx, self.d_ro_emo, d_rows, self.d_ro_dirs + UInt64(slot * self.ro_E * D * 4),
                start_pos, S, self.ro_E, n_emo, slot,
            )
    if layer == self.ro_emo_last:
        readout_launch_mark(self.ctx, self.d_ro_emo_valid, start_pos, S)
    if not staging:
        return
    for slot in range(n_ws):
        if self.ro_ws_layers[slot] == layer:
            readout_launch_copy(self.ctx, self.d_ro_stage + UInt64(slot * RO_STAGE_ROWS * D * 4), d_rows, S * D)


def readout_configure(
    mut self: GemmaEngine, emo_layers: List[Int], E: Int, dirs_host: UInt64,
    ws_layers: List[Int], k: Int, lens_host: UInt64, norm_w_host: UInt64,
) raises:
    """Replaces any previous readout state. The FFI wrapper has already validated every argument."""
    self.readout_free()
    var n_emo = len(emo_layers)
    var n_ws = len(ws_layers)
    var ms = self.max_seq
    self.d_ro_dirs = cuda_malloc(n_emo * E * D * 4)
    cuda_memcpy(self.d_ro_dirs, dirs_host, n_emo * E * D * 4, 1)
    self.d_ro_emo = cuda_malloc(ms * n_emo * (E + 1) * 4)
    self.d_ro_emo_valid = cuda_malloc(ms)
    cuda_memset_2d(self.d_ro_emo_valid, ms, 0, ms, 1)
    self.ro_emo_layers = emo_layers.copy()
    self.ro_E = E
    self.ro_emo_last = -1
    for i in range(n_emo):
        if emo_layers[i] > self.ro_emo_last:
            self.ro_emo_last = emo_layers[i]
    if n_ws > 0:
        self.d_ro_lens = cuda_malloc(n_ws * D * D * 4)
        cuda_memcpy(self.d_ro_lens, lens_host, n_ws * D * D * 4, 1)
        self.d_ro_norm_w = cuda_malloc(D * 4)
        cuda_memcpy(self.d_ro_norm_w, norm_w_host, D * 4, 1)
        self.d_ro_stage = cuda_malloc(n_ws * RO_STAGE_ROWS * D * 4)
        self.d_ro_lensed = cuda_malloc(n_ws * RO_STAGE_ROWS * D * 4)
        self.d_ro_normed = cuda_malloc(n_ws * RO_STAGE_ROWS * D * 4)
        self.d_ro_logits = cuda_malloc(NVFP4_GEMV_SMAX * VOCAB * 4)
        self.d_ro_ws_ids = cuda_malloc(ms * n_ws * k * 4)
        self.d_ro_ws_scores = cuda_malloc(ms * n_ws * k * 4)
        self.d_ro_ws_valid = cuda_malloc(ms)
        cuda_memset_2d(self.d_ro_ws_valid, ms, 0, ms, 1)
        self.d_ro_bad = cuda_malloc(n_ws * RO_STAGE_ROWS * 4)
        self.ro_ws_layers = ws_layers.copy()
        self.ro_k = k
    cuda_sync()
    self.ro_stage_pos = List[Int]()
    self.ro_active = True


def readout_begin_turn(mut self: GemmaEngine) raises:
    """Clears every position's validity: a turn's readouts are exactly what its own forwards write (ruling R1)."""
    self.ctx.synchronize()
    if self.d_ro_emo_valid != UInt64(0):
        cuda_memset_2d(self.d_ro_emo_valid, self.max_seq, 0, self.max_seq, 1)
    if self.d_ro_ws_valid != UInt64(0):
        cuda_memset_2d(self.d_ro_ws_valid, self.max_seq, 0, self.max_seq, 1)
    cuda_sync()
    self.ro_stage_pos = List[Int]()


def readout_project_rows_debug(mut self: GemmaEngine, rows_host: UInt64, n: Int, slot: Int, out_host: UInt64) raises:
    """Test hook: the hook's projection kernel applied to host rows [n, D] for emotion slot `slot`; [n, E+1] to host."""
    var d_rows = cuda_malloc(n * D * 4)
    var d_out = cuda_malloc(n * (self.ro_E + 1) * 4)
    cuda_memcpy(d_rows, rows_host, n * D * 4, 1)
    readout_launch_proj(
        self.ctx, d_out, d_rows, self.d_ro_dirs + UInt64(slot * self.ro_E * D * 4), 0, n, self.ro_E, 1, 0,
    )
    self.ctx.synchronize()
    cuda_memcpy(out_host, d_out, n * (self.ro_E + 1) * 4, 2)
    cuda_free(d_rows)
    cuda_free(d_out)


def readout_commit(mut self: GemmaEngine, row0: Int, nrows: Int) raises -> Int:
    """J-lens top-k for staged rows [row0, row0 + nrows) of every workspace slot, written at their positions:
    - the lens, h @ J^T, per slot: cuBLAS on the default stream, between barriers;
    - the final RMS norm over all (slot, row) pairs;
    - the decode lm-head over all pairs: the exact NVFP4 multirow GEMV, NVFP4_GEMV_SMAX pairs per pass, so its
      weights stream once per pass rather than once per slot;
    - top-k per pair, which refuses a pair whose logits hold a non-finite value (Codex 10-26 F1).
    Each position becomes workspace-valid when all its pairs were ranked, and invalid otherwise. Returns how many
    positions were refused (0 when every pair was ranked)."""
    var n_ws = len(self.ro_ws_layers)
    var total = n_ws * nrows
    self.ctx.synchronize()          # the staged rows were written by ctx kernels
    for slot in range(n_ws):
        cublas_sgemm(
            self.handle,
            self.d_ro_stage + UInt64((slot * RO_STAGE_ROWS + row0) * D * 4),
            self.d_ro_lens + UInt64(slot * D * D * 4),
            self.d_ro_lensed + UInt64(slot * nrows * D * 4),
            nrows, D, D,
        )
    cuda_sync()
    gpu_rmsnorm_batched_mojo(self.ctx, self.d_ro_lensed, self.d_ro_normed, self.d_ro_norm_w, D, total,
                             Float32(RMS_EPS_FINAL))
    var p0 = 0
    while p0 < total:
        var cs = total - p0
        if cs > NVFP4_GEMV_SMAX:
            cs = NVFP4_GEMV_SMAX
        gpu_matmul_nvfp4_fused_v3_multirow_dev(
            self.ctx, self.d_ro_logits, self.d_ro_normed + UInt64(p0 * D * 4),
            self.d_embed_nvfp4, self.embed_global, D, VOCAB, cs,
        )
        for i in range(cs):
            var slot = (p0 + i) // nrows
            var pos = self.ro_stage_pos[row0 + (p0 + i) % nrows]
            readout_launch_topk(
                self.ctx, self.d_ro_ws_ids, self.d_ro_ws_scores, self.d_ro_logits + UInt64(i * VOCAB * 4),
                VOCAB, self.ro_k, (pos * n_ws + slot) * self.ro_k, self.d_ro_bad, p0 + i,
            )
        p0 += cs
    readout_launch_mark_kept(self.ctx, self.d_ro_ws_valid, self.d_ro_bad, self.ro_stage_pos[row0], nrows, n_ws)
    self.ctx.synchronize()
    var flags = List[Int32](capacity=total)
    for _ in range(total):
        flags.append(Int32(0))
    cuda_download_i32(flags, self.d_ro_bad, total)
    var refused = 0
    for r in range(nrows):
        for slot in range(n_ws):
            if flags[slot * nrows + r] != Int32(0):
                refused += 1
                break
    if refused > 0:
        print("[readout] workspace commit: ", refused, " of ", nrows, " positions from ", self.ro_stage_pos[row0],
              " had non-finite logits and stay workspace-invalid")
    return refused


def readout_topk_debug(
    mut self: GemmaEngine, logits_host: UInt64, k: Int, ids_host: UInt64, scores_host: UInt64,
) raises -> Bool:
    """Test hook: readout_topk_kernel on host logits [VOCAB]. Returns True when the kernel refused them (a non-finite
    logit); ids and scores are then unspecified."""
    var d_lg = cuda_malloc(VOCAB * 4)
    var d_ids = cuda_malloc(k * 4)
    var d_sc = cuda_malloc(k * 4)
    var d_bad = cuda_malloc(4)
    cuda_memcpy(d_lg, logits_host, VOCAB * 4, 1)
    readout_launch_topk(self.ctx, d_ids, d_sc, d_lg, VOCAB, k, 0, d_bad, 0)
    self.ctx.synchronize()
    cuda_memcpy(ids_host, d_ids, k * 4, 2)
    cuda_memcpy(scores_host, d_sc, k * 4, 2)
    var flag = List[Int32](capacity=1)
    flag.append(Int32(0))
    cuda_download_i32(flag, d_bad, 1)
    cuda_free(d_lg)
    cuda_free(d_ids)
    cuda_free(d_sc)
    cuda_free(d_bad)
    return flag[0] != Int32(0)


def readout_stage_debug(mut self: GemmaEngine, out_host: UInt64, n: Int) raises:
    """Test hook: the first n staged rows of every workspace slot, to host as [n_ws, n, D]."""
    self.ctx.synchronize()
    for slot in range(len(self.ro_ws_layers)):
        cuda_memcpy(out_host + UInt64(slot * n * D * 4), self.d_ro_stage + UInt64(slot * RO_STAGE_ROWS * D * 4),
                    n * D * 4, 2)


def ro_layers_from_host(ptr: Int64, n: Int) -> List[Int]:
    """Layer ids from host i32[n]: distinct and in [0, TOTAL_LAYERS), else an empty list."""
    var p = UnsafePointer[Int32, MutAnyOrigin](unsafe_from_address=Int(ptr))
    var out = List[Int]()
    for i in range(n):
        var l = Int(p[i])
        if l < 0 or l >= TOTAL_LAYERS:
            return List[Int]()
        for j in range(len(out)):
            if out[j] == l:
                return List[Int]()
        out.append(l)
    return out^


@always_inline
def _ro_bad(v: Float32) -> Bool:
    return not (v == v) or v > Float32(3.0e38) or v < Float32(-3.0e38)


def ro_finite_host(ptr: Int64, n: Int) -> Bool:
    """Every one of n host floats is finite."""
    var p = UnsafePointer[Float32, MutAnyOrigin](unsafe_from_address=Int(ptr))
    for i in range(n):
        if _ro_bad(p[i]):
            return False
    return True


def ro_unit_rows_host(ptr: Int64, rows: Int) -> Bool:
    """rows x D host floats: every row finite, with |norm - 1| <= 1e-5."""
    var p = UnsafePointer[Float32, MutAnyOrigin](unsafe_from_address=Int(ptr))
    for r in range(rows):
        var ss = Float64(0.0)
        for i in range(D):
            var v = p[r * D + i]
            if _ro_bad(v):
                return False
            ss += Float64(v) * Float64(v)
        var nrm = sqrt(ss)
        if nrm < 1.0 - 1e-5 or nrm > 1.0 + 1e-5:
            return False
    return True
