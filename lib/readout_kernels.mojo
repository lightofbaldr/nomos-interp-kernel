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
