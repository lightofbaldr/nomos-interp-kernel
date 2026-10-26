"""Readout spine GPU kernels and their launchers (glimmer-emotions docs/superpowers/specs/2026-10-20-readout-spine-design.md).

They live apart from lib/engine_readout.mojo, which imports GemmaEngine and is reached from the engine's own import
cycle (gemma4_engine -> engine_decode/engine_prefill -> engine_readout): a kernel compiled from inside that cycle fails
to infer its argument types. Like the other kernel modules here, this one takes a DeviceContext and raw device
addresses, and never imports the engine."""

from max.gpu.host import DeviceContext
from std.gpu.primitives import thread_idx, block_idx, block_dim
from max.gpu import barrier
from max.gpu.memory import AddressSpace
from std.memory import UnsafePointer
from std.math import sqrt
from layout import row_major
from layout import stack_allocation
from lib.q4_weights import _as_f32_ptr
from lib.model_config import D

comptime RO_TPB = 256
comptime RO_TOPK_TPB = 1024
comptime RO_MAX_K = 16


@always_inline
def _ro_u8(addr: UInt64) -> UnsafePointer[UInt8, MutAnyOrigin]:
    return UnsafePointer[UInt8, MutAnyOrigin](unsafe_from_address=Int(addr))


@always_inline
def _ro_i32(addr: UInt64) -> UnsafePointer[Int32, MutAnyOrigin]:
    return UnsafePointer[Int32, MutAnyOrigin](unsafe_from_address=Int(addr))


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


def readout_copy_kernel(
    dst: UnsafePointer[Float32, MutAnyOrigin], src: UnsafePointer[Float32, MutAnyOrigin], n_arg: Int32
):
    """dst[i] = src[i] for i < n, on ctx's stream (ordered after the kernels that wrote src)."""
    var i = Int(block_idx.x) * Int(block_dim.x) + Int(thread_idx.x)
    if i < Int(n_arg):
        dst[i] = src[i]


def readout_topk_kernel(
    ids: UnsafePointer[Int32, MutAnyOrigin],
    scores: UnsafePointer[Float32, MutAnyOrigin],
    logits: UnsafePointer[Float32, MutAnyOrigin],   # one row [vocab]
    vocab_arg: Int32,
    k_arg: Int32,
    off_arg: Int32,
):
    """k rounds of block argmax, in one block; a chosen id is excluded from later rounds. The order is the higher
    score first and, on equal scores, the lower index first: the first k of a stable descending sort."""
    var n = Int(vocab_arg)
    var kk = Int(k_arg)
    var off = Int(off_arg)
    var score_s = stack_allocation[dtype=DType.float32, address_space=AddressSpace.SHARED](row_major[RO_TOPK_TPB]())
    var idx_s = stack_allocation[dtype=DType.int32, address_space=AddressSpace.SHARED](row_major[RO_TOPK_TPB]())
    var chosen = stack_allocation[dtype=DType.int32, address_space=AddressSpace.SHARED](row_major[RO_MAX_K]())
    var tid = Int(thread_idx.x)
    for rnd in range(kk):
        var best = Float32(-3.4e38)
        var best_i = Int32(-1)
        var i = tid
        while i < n:
            var skip = False
            for c in range(rnd):
                if Int(chosen[c]) == i:
                    skip = True
            if not skip:
                var v = logits[i]
                if best_i < 0 or v > best:
                    best = v
                    best_i = Int32(i)
            i += RO_TOPK_TPB
        score_s[tid] = best
        idx_s[tid] = best_i
        barrier()
        var stride = RO_TOPK_TPB // 2
        while stride > 0:
            if tid < stride:
                var o = tid + stride
                var oi = idx_s[o]
                if oi >= 0:
                    var mi = idx_s[tid]
                    if mi < 0 or score_s[o] > score_s[tid] or (score_s[o] == score_s[tid] and oi < mi):
                        score_s[tid] = score_s[o]
                        idx_s[tid] = oi
            barrier()
            stride //= 2
        if tid == 0:
            chosen[rnd] = idx_s[0]
            ids[off + rnd] = idx_s[0]
            scores[off + rnd] = score_s[0]
        barrier()


def readout_launch_proj(
    ctx: DeviceContext, d_table: UInt64, d_rows: UInt64, d_dirs: UInt64,
    start_pos: Int, S: Int, E: Int, n_emo: Int, slot: Int,
) raises:
    """Enqueues readout_proj_kernel on ctx: S rows [S, D] onto E unit directions [E, D], plus each row's L2 norm,
    into table rows start_pos.. at emotion slot `slot` of n_emo."""
    var kp = ctx.compile_function[readout_proj_kernel]()
    ctx.enqueue_function(
        kp, _as_f32_ptr(d_table), _as_f32_ptr(d_rows), _as_f32_ptr(d_dirs),
        Int32(start_pos), Int32(S), Int32(E), Int32(n_emo), Int32(slot),
        grid_dim=S * (E + 1), block_dim=RO_TPB,
    )


def readout_launch_mark(ctx: DeviceContext, d_valid: UInt64, start_pos: Int, n: Int) raises:
    """Enqueues readout_mark_kernel on ctx: valid[start_pos + i] = 1 for i < n."""
    var km = ctx.compile_function[readout_mark_kernel]()
    ctx.enqueue_function(
        km, _ro_u8(d_valid), Int32(start_pos), Int32(n),
        grid_dim=(n + RO_TPB - 1) // RO_TPB, block_dim=RO_TPB,
    )


def readout_launch_copy(ctx: DeviceContext, d_dst: UInt64, d_src: UInt64, n: Int) raises:
    """Enqueues readout_copy_kernel on ctx: n floats from d_src to d_dst, ordered after the kernels that wrote d_src."""
    var kc = ctx.compile_function[readout_copy_kernel]()
    ctx.enqueue_function(
        kc, _as_f32_ptr(d_dst), _as_f32_ptr(d_src), Int32(n),
        grid_dim=(n + RO_TPB - 1) // RO_TPB, block_dim=RO_TPB,
    )


def readout_launch_topk(
    ctx: DeviceContext, d_ids: UInt64, d_scores: UInt64, d_logits: UInt64, vocab: Int, k: Int, off: Int,
) raises:
    """Enqueues readout_topk_kernel on ctx: the top k of one logits row [vocab], written to ids and scores at off."""
    var kt = ctx.compile_function[readout_topk_kernel]()
    ctx.enqueue_function(
        kt, _ro_i32(d_ids), _as_f32_ptr(d_scores), _as_f32_ptr(d_logits), Int32(vocab), Int32(k), Int32(off),
        grid_dim=1, block_dim=RO_TOPK_TPB,
    )
