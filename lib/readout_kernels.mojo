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
comptime RO_TOPK_CHUNK = 2048       # logits per top-k stage-1 block
comptime RO_TOPK_TPB1 = 256         # top-k stage-1 threads per block


@always_inline
def _ro_u8(addr: UInt64) -> UnsafePointer[UInt8, MutAnyOrigin]:
    return UnsafePointer[UInt8, MutAnyOrigin](unsafe_from_address=Int(addr))


@always_inline
def _ro_i32(addr: UInt64) -> UnsafePointer[Int32, MutAnyOrigin]:
    return UnsafePointer[Int32, MutAnyOrigin](unsafe_from_address=Int(addr))


@always_inline
def _ro_nonfinite(v: Float32) -> Bool:
    """NaN, either infinity, or a magnitude past 3e38 (the host checks' bound): a logit no ranking can trust."""
    return not (v == v) or v > Float32(3.0e38) or v < Float32(-3.0e38)


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


def readout_topk_partial_kernel(
    cand_ids: UnsafePointer[Int32, MutAnyOrigin],       # [rows, nblk, k]
    cand_scores: UnsafePointer[Float32, MutAnyOrigin],  # [rows, nblk, k]
    cand_bad: UnsafePointer[Int32, MutAnyOrigin],       # [rows, nblk]
    logits: UnsafePointer[Float32, MutAnyOrigin],       # [rows, vocab]
    vocab_arg: Int32,
    k_arg: Int32,
    nblk_arg: Int32,
):
    """Top-k stage 1: one block per (row, chunk of RO_TOPK_CHUNK logits). k rounds of block argmax over the chunk, a
    chosen id excluded from later rounds; the order is the higher score first and, on equal scores, the lower id first.
    Round 0 also checks every logit: one non-finite value marks the chunk bad (Codex 10-26 F1) and ends the block. A
    chunk shorter than k pads with id -1."""
    var vocab = Int(vocab_arg)
    var kk = Int(k_arg)
    var nblk = Int(nblk_arg)
    var b = Int(block_idx.x)
    var row = b // nblk
    var lo = (b % nblk) * RO_TOPK_CHUNK
    var hi = lo + RO_TOPK_CHUNK
    if hi > vocab:
        hi = vocab
    var score_s = stack_allocation[dtype=DType.float32, address_space=AddressSpace.SHARED](row_major[RO_TOPK_TPB1]())
    var idx_s = stack_allocation[dtype=DType.int32, address_space=AddressSpace.SHARED](row_major[RO_TOPK_TPB1]())
    var chosen = stack_allocation[dtype=DType.int32, address_space=AddressSpace.SHARED](row_major[RO_MAX_K]())
    var flag_s = stack_allocation[dtype=DType.int32, address_space=AddressSpace.SHARED](row_major[1]())
    var tid = Int(thread_idx.x)
    if tid == 0:
        flag_s[0] = Int32(0)
    barrier()
    for rnd in range(kk):
        var best = Float32(-3.4e38)
        var best_i = Int32(-1)
        var seen = False
        var i = lo + tid
        while i < hi:
            var skip = False
            for c in range(rnd):
                if Int(chosen[c]) == i:
                    skip = True
            if not skip:
                var v = logits[row * vocab + i]
                if rnd == 0 and _ro_nonfinite(v):
                    seen = True
                if best_i < 0 or v > best:
                    best = v
                    best_i = Int32(i)
            i += RO_TOPK_TPB1
        if seen:
            flag_s[0] = Int32(1)
        score_s[tid] = best
        idx_s[tid] = best_i
        barrier()
        if rnd == 0:
            var refused = flag_s[0] != Int32(0)     # read by every thread after the barrier: a uniform decision
            if tid == 0:
                cand_bad[b] = Int32(1) if refused else Int32(0)
            if refused:
                return
        var stride = RO_TOPK_TPB1 // 2
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
            cand_ids[b * kk + rnd] = idx_s[0]
            cand_scores[b * kk + rnd] = score_s[0]
        barrier()


def readout_topk_merge_kernel(
    ids: UnsafePointer[Int32, MutAnyOrigin],
    scores: UnsafePointer[Float32, MutAnyOrigin],
    bad: UnsafePointer[Int32, MutAnyOrigin],            # one flag per (slot, row) pair: 1 = refused
    cand_ids: UnsafePointer[Int32, MutAnyOrigin],
    cand_scores: UnsafePointer[Float32, MutAnyOrigin],
    cand_bad: UnsafePointer[Int32, MutAnyOrigin],
    k_arg: Int32,
    nblk_arg: Int32,
    pair0_arg: Int32,
    nrows_arg: Int32,
    n_ws_arg: Int32,
    pos0_arg: Int32,
):
    """Top-k stage 2: one block per logits row of the pass, merging the row's nblk * k stage-1 candidates in k rounds
    of block argmax, in the same order. Row r is pair p = pair0 + r: slot p // nrows at position pos0 + p % nrows, so
    its top k go to offset (position * n_ws + slot) * k. A bad chunk refuses the row: bad[p] = 1 and nothing is written;
    otherwise bad[p] = 0. The global top k is a subset of the chunks' top k, so the merge is exact."""
    var kk = Int(k_arg)
    var nblk = Int(nblk_arg)
    var r = Int(block_idx.x)
    var pair = Int(pair0_arg) + r
    var nrows = Int(nrows_arg)
    var off = ((Int(pos0_arg) + pair % nrows) * Int(n_ws_arg) + pair // nrows) * kk
    var ncand = nblk * kk
    var cbase = r * ncand
    var score_s = stack_allocation[dtype=DType.float32, address_space=AddressSpace.SHARED](row_major[RO_TOPK_TPB]())
    var slot_s = stack_allocation[dtype=DType.int32, address_space=AddressSpace.SHARED](row_major[RO_TOPK_TPB]())
    var id_s = stack_allocation[dtype=DType.int32, address_space=AddressSpace.SHARED](row_major[RO_TOPK_TPB]())
    var chosen = stack_allocation[dtype=DType.int32, address_space=AddressSpace.SHARED](row_major[RO_MAX_K]())
    var flag_s = stack_allocation[dtype=DType.int32, address_space=AddressSpace.SHARED](row_major[1]())
    var tid = Int(thread_idx.x)
    if tid == 0:
        flag_s[0] = Int32(0)
    barrier()
    var j = tid
    while j < nblk:
        if cand_bad[r * nblk + j] != Int32(0):
            flag_s[0] = Int32(1)
        j += RO_TOPK_TPB
    barrier()
    var refused = flag_s[0] != Int32(0)             # read by every thread after the barrier: a uniform decision
    if tid == 0:
        bad[pair] = Int32(1) if refused else Int32(0)
    if refused:
        return
    for rnd in range(kk):
        var best = Float32(-3.4e38)
        var best_q = Int32(-1)
        var best_id = Int32(-1)
        var q = tid
        while q < ncand:
            var cid = cand_ids[cbase + q]
            if cid >= 0:
                var skip = False
                for c in range(rnd):
                    if Int(chosen[c]) == q:
                        skip = True
                if not skip:
                    var v = cand_scores[cbase + q]
                    if best_q < 0 or v > best or (v == best and cid < best_id):
                        best = v
                        best_q = Int32(q)
                        best_id = cid
            q += RO_TOPK_TPB
        score_s[tid] = best
        slot_s[tid] = best_q
        id_s[tid] = best_id
        barrier()
        var stride = RO_TOPK_TPB // 2
        while stride > 0:
            if tid < stride:
                var o = tid + stride
                if slot_s[o] >= 0:
                    var mine = slot_s[tid]
                    if mine < 0 or score_s[o] > score_s[tid] or (score_s[o] == score_s[tid] and id_s[o] < id_s[tid]):
                        score_s[tid] = score_s[o]
                        slot_s[tid] = slot_s[o]
                        id_s[tid] = id_s[o]
            barrier()
            stride //= 2
        if tid == 0:
            chosen[rnd] = slot_s[0]
            ids[off + rnd] = id_s[0]
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


def readout_topk_blocks(vocab: Int) -> Int:
    """Top-k stage-1 blocks per logits row."""
    return (vocab + RO_TOPK_CHUNK - 1) // RO_TOPK_CHUNK


def readout_topk_scratch_bytes(vocab: Int, rows: Int) -> Int:
    """Device scratch the two-stage top-k needs for `rows` logits rows at any k <= RO_MAX_K."""
    return rows * readout_topk_blocks(vocab) * (RO_MAX_K * 8 + 4)


def readout_launch_topk(
    ctx: DeviceContext, d_ids: UInt64, d_scores: UInt64, d_bad: UInt64, d_logits: UInt64, vocab: Int, k: Int,
    rows: Int, d_scratch: UInt64, pair0: Int, nrows: Int, n_ws: Int, pos0: Int,
) raises:
    """Enqueues the two-stage top-k on ctx for `rows` logits rows [rows, vocab], within readout_topk_scratch_bytes(
    vocab, rows) of scratch: stage 1 finds each chunk's top k, stage 2 merges them per row and writes the row's top k,
    or refuses the row (bad[pair] = 1) when it holds a non-finite logit. Row r is pair pair0 + r; see
    readout_topk_merge_kernel for where its result goes."""
    var nblk = readout_topk_blocks(vocab)
    var n_c = rows * nblk * k
    var d_cid = d_scratch
    var d_csc = d_scratch + UInt64(n_c * 4)
    var d_cbad = d_scratch + UInt64(n_c * 8)
    var k1 = ctx.compile_function[readout_topk_partial_kernel]()
    ctx.enqueue_function(
        k1, _ro_i32(d_cid), _as_f32_ptr(d_csc), _ro_i32(d_cbad), _as_f32_ptr(d_logits),
        Int32(vocab), Int32(k), Int32(nblk),
        grid_dim=rows * nblk, block_dim=RO_TOPK_TPB1,
    )
    var k2 = ctx.compile_function[readout_topk_merge_kernel]()
    ctx.enqueue_function(
        k2, _ro_i32(d_ids), _as_f32_ptr(d_scores), _ro_i32(d_bad), _ro_i32(d_cid), _as_f32_ptr(d_csc),
        _ro_i32(d_cbad), Int32(k), Int32(nblk), Int32(pair0), Int32(nrows), Int32(n_ws), Int32(pos0),
        grid_dim=rows, block_dim=RO_TOPK_TPB,
    )


def readout_mark_kept_kernel(
    valid: UnsafePointer[UInt8, MutAnyOrigin], bad: UnsafePointer[Int32, MutAnyOrigin],
    start_pos_arg: Int32, n_arg: Int32, n_ws_arg: Int32,
):
    """valid[start_pos + r] for r < n: 1 when all n_ws of its (slot, row) pairs were ranked (pair = slot * n + r),
    else 0. A refused position is cleared, so no earlier commit's result stays readable there."""
    var r = Int(block_idx.x) * Int(block_dim.x) + Int(thread_idx.x)
    var n = Int(n_arg)
    if r < n:
        var ok = True
        for slot in range(Int(n_ws_arg)):
            if bad[slot * n + r] != Int32(0):
                ok = False
        valid[Int(start_pos_arg) + r] = UInt8(1) if ok else UInt8(0)


def readout_launch_mark_kept(
    ctx: DeviceContext, d_valid: UInt64, d_bad: UInt64, start_pos: Int, n: Int, n_ws: Int,
) raises:
    """Enqueues readout_mark_kept_kernel on ctx: each of n positions from start_pos becomes workspace-valid only if all
    its n_ws pairs were ranked."""
    var km = ctx.compile_function[readout_mark_kept_kernel]()
    ctx.enqueue_function(
        km, _ro_u8(d_valid), _ro_i32(d_bad), Int32(start_pos), Int32(n), Int32(n_ws),
        grid_dim=(n + RO_TPB - 1) // RO_TPB, block_dim=RO_TPB,
    )
