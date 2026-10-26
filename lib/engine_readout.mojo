"""Readout spine (glimmer-emotions docs/superpowers/specs/2026-10-20-readout-spine-design.md, section 2).

At a configured layer, right after plan B's steering point (post-steer), each computed row's projections onto unit
emotion directions, and its residual L2 norm, go into a per-position table. A position reads valid only if a forward
wrote it since the last begin_turn and it lies below the cache length at read time."""

from max.gpu.host import DeviceContext
from std.gpu.primitives import thread_idx, block_idx, block_dim
from max.gpu import barrier
from max.gpu.memory import AddressSpace
from std.memory import UnsafePointer
from std.math import sqrt
from layout import row_major
from layout import stack_allocation
from lib.cuda import cuda_malloc, cuda_free, cuda_memcpy, cuda_sync, cuda_memset_2d
from lib.q4_weights import _as_f32_ptr
from lib.fp4_gemv_v2 import NVFP4_GEMV_SMAX
from lib.dflash_drafter import DFLASH_MAX_VERIFY_ROWS
from lib.model_config import D, VOCAB, TOTAL_LAYERS
from lib.gemma4_engine import GemmaEngine

comptime RO_TPB = 256
comptime RO_STAGE_ROWS = DFLASH_MAX_VERIFY_ROWS   # a DFlash verify stages at most this many rows; a decode step 1


@always_inline
def _ro_u8(addr: UInt64) -> UnsafePointer[UInt8, MutAnyOrigin]:
    return UnsafePointer[UInt8, MutAnyOrigin](unsafe_from_address=Int(addr))


def readout_proj_kernel(
    table: UnsafePointer[Float32, MutAnyOrigin],   # [positions, n_emo, E+1]
    rows: UnsafePointer[Float32, MutAnyOrigin],    # [S, D]
    dirs: UnsafePointer[Float32, MutAnyOrigin],    # [E, D]: this slot's unit directions
    start_pos_arg: Int32,
    S_arg: Int32,
    E_arg: Int32,
    n_emo_arg: Int32,
    slot_arg: Int32,
):
    """One block per (row, output). Outputs 0..E-1 are row . dirs[e]; output E is the row's L2 norm. A fixed
    per-thread stride and a fixed tree make every result deterministic."""
    var E = Int(E_arg)
    var b = Int(block_idx.x)
    var r = b // (E + 1)
    var e = b % (E + 1)
    if r >= Int(S_arg):
        return
    var acc_s = stack_allocation[dtype=DType.float32, address_space=AddressSpace.SHARED](row_major[RO_TPB]())
    var tid = Int(thread_idx.x)
    var acc = Float32(0.0)
    var i = tid
    if e < E:
        while i < D:
            acc += rows[r * D + i] * dirs[e * D + i]
            i += RO_TPB
    else:
        while i < D:
            var x = rows[r * D + i]
            acc += x * x
            i += RO_TPB
    acc_s[tid] = acc
    barrier()
    var stride = RO_TPB // 2
    while stride > 0:
        if tid < stride:
            acc_s[tid] = acc_s[tid] + acc_s[tid + stride]
        barrier()
        stride //= 2
    if tid == 0:
        var v = acc_s[0]
        if e == E:
            v = sqrt(v)
        var pos = Int(start_pos_arg) + r
        table[(pos * Int(n_emo_arg) + Int(slot_arg)) * (E + 1) + e] = v


def readout_mark_kernel(valid: UnsafePointer[UInt8, MutAnyOrigin], start_pos_arg: Int32, n_arg: Int32):
    """valid[start_pos + i] = 1 for i < n."""
    var i = Int(block_idx.x) * Int(block_dim.x) + Int(thread_idx.x)
    if i < Int(n_arg):
        valid[Int(start_pos_arg) + i] = UInt8(1)


def readout_rows(mut self: GemmaEngine, d_rows: UInt64, S: Int, start_pos: Int, layer: Int, stage: Bool) raises:
    """The hook, right after the steering point of `layer`, for S rows at positions start_pos..
    `stage` is True for a verify call's rows and for a decode step's row: the rows that can get a workspace readout."""
    if not self.ro_active:
        return
    var n_emo = len(self.ro_emo_layers)
    for slot in range(n_emo):
        if self.ro_emo_layers[slot] == layer:
            var kp = self.ctx.compile_function[readout_proj_kernel]()
            self.ctx.enqueue_function(
                kp, _as_f32_ptr(self.d_ro_emo), _as_f32_ptr(d_rows),
                _as_f32_ptr(self.d_ro_dirs + UInt64(slot * self.ro_E * D * 4)),
                Int32(start_pos), Int32(S), Int32(self.ro_E), Int32(n_emo), Int32(slot),
                grid_dim=S * (self.ro_E + 1), block_dim=RO_TPB,
            )
    if layer == self.ro_emo_last:
        var km = self.ctx.compile_function[readout_mark_kernel]()
        self.ctx.enqueue_function(
            km, _ro_u8(self.d_ro_emo_valid), Int32(start_pos), Int32(S),
            grid_dim=(S + RO_TPB - 1) // RO_TPB, block_dim=RO_TPB,
        )


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
    var kp = self.ctx.compile_function[readout_proj_kernel]()
    self.ctx.enqueue_function(
        kp, _as_f32_ptr(d_out), _as_f32_ptr(d_rows),
        _as_f32_ptr(self.d_ro_dirs + UInt64(slot * self.ro_E * D * 4)),
        Int32(0), Int32(n), Int32(self.ro_E), Int32(1), Int32(0),
        grid_dim=n * (self.ro_E + 1), block_dim=RO_TPB,
    )
    self.ctx.synchronize()
    cuda_memcpy(out_host, d_out, n * (self.ro_E + 1) * 4, 2)
    cuda_free(d_rows)
    cuda_free(d_out)


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
