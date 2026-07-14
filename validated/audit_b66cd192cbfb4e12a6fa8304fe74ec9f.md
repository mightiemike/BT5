### Title
Dead `DISABLE_OP` Guard in `op_div` / `op_divmod` / `op_mod` Allows Consensus/Mempool Limit Divergence - (File: src/more_ops.rs)

### Summary

In `src/more_ops.rs`, the `ClvmFlags::DISABLE_OP` guard intended to enforce a stricter operand-size limit in mempool mode for `op_div`, `op_divmod`, and `op_mod` (and their malachite variants) is dead code. The guard checks `a0_len > 2048`, but an unconditional check immediately below it checks `a0_len > 256`. Because 256 < 2048, the unconditional check always fires first, making the `DISABLE_OP` branch permanently unreachable. The two checks that should be complementary (one for mempool, one for consensus) have their thresholds inverted, exactly mirroring the external report's pattern of two guards that should differ but are functionally identical.

### Finding Description

`DISABLE_OP` is a flag included in `MEMPOOL_MODE` (the stricter validation mode used by the mempool):

```rust
pub const MEMPOOL_MODE: ClvmFlags = ClvmFlags::NO_UNKNOWN_OPS
    .union(ClvmFlags::LIMIT_HEAP)
    .union(ClvmFlags::DISABLE_OP)   // <-- included here
    .union(ClvmFlags::CANONICAL_INTS)
    .union(ClvmFlags::LIMIT_SOFTFORK);
``` [1](#0-0) 

In `op_div`, the guard pair reads:

```rust
if flags.contains(ClvmFlags::DISABLE_OP) && a0_len > 2048 {   // line 665 — dead
    return Err(EvalErr::InvalidOpArg(input, "div".to_string()));
}
if a0_len > 256 || a1_len > 1024 {                             // line 668 — always fires first
    return Err(EvalErr::InvalidOpArg(input, "div".to_string()));
}
``` [2](#0-1) 

Because `256 < 2048`, any input that would trigger the `DISABLE_OP` branch (`a0_len > 2048`) is already caught by the unconditional branch (`a0_len > 256`). The `DISABLE_OP` guard is therefore permanently unreachable — it can never fire. The identical structural defect appears in all six affected functions:

| Function | Lines |
|---|---|
| `op_div` | 665–669 |
| `op_div_malachite` | 690–694 |
| `op_divmod` | 713–717 |
| `op_divmod_malachite` | 742–746 |
| `op_mod` | 769–773 |
| `op_mod_malachite` | 794–798 | [3](#0-2) [4](#0-3) 

The intended design is clear from the structure: the `DISABLE_OP` branch was meant to enforce a tighter mempool-mode limit on `a0_len` (256 bytes), while the unconditional branch was meant to enforce the looser consensus-mode limit (2048 bytes). The thresholds are swapped. The correct code should be:

```rust
// mempool: stricter
if flags.contains(ClvmFlags::DISABLE_OP) && a0_len > 256 {
    return Err(...);
}
// consensus: looser
if a0_len > 2048 || a1_len > 1024 {
    return Err(...);
}
```

### Impact Explanation

With the thresholds swapped, both consensus mode and mempool mode apply the 256-byte limit to `a0_len`. The intended consensus-mode limit of 2048 bytes is never enforced; instead, the mempool limit is silently applied everywhere. Any CLVM program that passes `op_div`, `op_divmod`, or `op_mod` a dividend atom between 257 and 2048 bytes is rejected by consensus nodes with `EvalErr::InvalidOpArg` when it should succeed. This is a **consensus divergence** bug: a node running a corrected build would accept such a transaction while the current build rejects it, causing a fork in chain state evaluation.

### Likelihood Explanation

`DISABLE_OP` is part of the exported Python API (`wheel/src/api.rs` line 322) and is included in `MEMPOOL_MODE`. Any caller — including the Chia full node — that runs generators in mempool mode passes `DISABLE_OP`. The bug is triggered by any attacker-controlled CLVM program that invokes opcode 19 (`/`), 20 (`divmod`), or 61 (`%`) with a dividend atom larger than 256 bytes. Crafting such a program requires only knowledge of the CLVM serialization format, which is fully public. [5](#0-4) 

### Recommendation

Swap the thresholds so the `DISABLE_OP` guard uses the stricter value (256) and the unconditional guard uses the looser value (2048), in all six affected functions (`op_div`, `op_div_malachite`, `op_divmod`, `op_divmod_malachite`, `op_mod`, `op_mod_malachite`). Add unit tests that explicitly verify:
- With `DISABLE_OP` set, `a0_len = 257` is rejected.
- Without `DISABLE_OP`, `a0_len = 257` (up to 2048) is accepted.

### Proof of Concept

```
# Consensus mode (no DISABLE_OP): should accept a0_len = 300, but currently rejects
program = (/ <300-byte-atom> <1-byte-atom>)
run_program(allocator, ChiaDialect::new(ClvmFlags::empty()), program, args, max_cost)
# → EvalErr::InvalidOpArg("div")   ← WRONG, should succeed

# Mempool mode (DISABLE_OP set): correctly rejects a0_len = 300
run_program(allocator, ChiaDialect::new(MEMPOOL_MODE), program, args, max_cost)
# → EvalErr::InvalidOpArg("div")   ← correct, but for the wrong reason (dead guard)
```

The `DISABLE_OP` branch at line 665 (`a0_len > 2048`) is never reached in either mode. Both modes fall through to the unconditional `a0_len > 256` check, collapsing two distinct validation tiers into one and making the mempool/consensus distinction for these operators inoperative. [6](#0-5) [7](#0-6)

### Citations

**File:** src/chia_dialect.rs (L70-76)
```rust
/// The default mode when running generators in mempool-mode (i.e. the stricter
/// mode).
pub const MEMPOOL_MODE: ClvmFlags = ClvmFlags::NO_UNKNOWN_OPS
    .union(ClvmFlags::LIMIT_HEAP)
    .union(ClvmFlags::DISABLE_OP)
    .union(ClvmFlags::CANONICAL_INTS)
    .union(ClvmFlags::LIMIT_SOFTFORK);
```

**File:** src/more_ops.rs (L658-679)
```rust
pub fn op_div(a: &mut Allocator, input: NodePtr, max_cost: Cost, flags: ClvmFlags) -> Response {
    if flags.contains(ClvmFlags::MALACHITE) {
        return op_div_malachite(a, input, max_cost, flags);
    }
    let [v0, v1] = get_args::<2>(a, input, "/")?;
    let (a0, a0_len) = int_atom(a, v0, "/")?;
    let (a1, a1_len) = int_atom(a, v1, "/")?;
    if flags.contains(ClvmFlags::DISABLE_OP) && a0_len > 2048 {
        return Err(EvalErr::InvalidOpArg(input, "div".to_string()));
    }
    if a0_len > 256 || a1_len > 1024 {
        return Err(EvalErr::InvalidOpArg(input, "div".to_string()));
    }
    let cost = DIV_BASE_COST + ((a0_len + a1_len) as Cost) * DIV_COST_PER_BYTE;
    check_cost(cost, max_cost)?;
    if a1.sign() == Sign::NoSign {
        return Err(EvalErr::DivisionByZero(input));
    }
    let q = a0.div_floor(&a1);
    let q = a.new_number(q)?;
    Ok(malloc_cost(a, cost, q))
}
```

**File:** src/more_ops.rs (L681-704)
```rust
fn op_div_malachite(
    a: &mut Allocator,
    input: NodePtr,
    max_cost: Cost,
    flags: ClvmFlags,
) -> Response {
    let [v0, v1] = get_args::<2>(a, input, "/")?;
    let (a0, a0_len) = malachite_int_atom(a, v0, "/")?;
    let (a1, a1_len) = malachite_int_atom(a, v1, "/")?;
    if flags.contains(ClvmFlags::DISABLE_OP) && a0_len > 2048 {
        return Err(EvalErr::InvalidOpArg(input, "div".to_string()));
    }
    if a0_len > 256 || a1_len > 1024 {
        return Err(EvalErr::InvalidOpArg(input, "div".to_string()));
    }
    let cost = DIV_BASE_COST + ((a0_len + a1_len) as Cost) * DIV_COST_PER_BYTE;
    check_cost(cost, max_cost)?;
    if a1.sign() == malachite_bigint::Sign::NoSign {
        return Err(EvalErr::DivisionByZero(input));
    }
    let q = a0.div_floor(&a1);
    let q = a.new_malachite_number(q)?;
    Ok(malloc_cost(a, cost, q))
}
```

**File:** src/more_ops.rs (L706-717)
```rust
pub fn op_divmod(a: &mut Allocator, input: NodePtr, max_cost: Cost, flags: ClvmFlags) -> Response {
    if flags.contains(ClvmFlags::MALACHITE) {
        return op_divmod_malachite(a, input, max_cost, flags);
    }
    let [v0, v1] = get_args::<2>(a, input, "divmod")?;
    let (a0, a0_len) = int_atom(a, v0, "divmod")?;
    let (a1, a1_len) = int_atom(a, v1, "divmod")?;
    if flags.contains(ClvmFlags::DISABLE_OP) && a0_len > 2048 {
        return Err(EvalErr::InvalidOpArg(input, "divmod".to_string()));
    }
    if a0_len > 256 || a1_len > 1024 {
        return Err(EvalErr::InvalidOpArg(input, "divmod".to_string()));
```

**File:** src/more_ops.rs (L762-773)
```rust
pub fn op_mod(a: &mut Allocator, input: NodePtr, max_cost: Cost, flags: ClvmFlags) -> Response {
    if flags.contains(ClvmFlags::MALACHITE) {
        return op_mod_malachite(a, input, max_cost, flags);
    }
    let [v0, v1] = get_args::<2>(a, input, "mod")?;
    let (a0, a0_len) = int_atom(a, v0, "mod")?;
    let (a1, a1_len) = int_atom(a, v1, "mod")?;
    if flags.contains(ClvmFlags::DISABLE_OP) && a0_len > 2048 {
        return Err(EvalErr::InvalidOpArg(input, "mod".to_string()));
    }
    if a0_len > 256 || a1_len > 1024 {
        return Err(EvalErr::InvalidOpArg(input, "mod".to_string()));
```

**File:** wheel/src/api.rs (L317-323)
```rust
    m.add("NO_UNKNOWN_OPS", ClvmFlags::NO_UNKNOWN_OPS.bits())?;
    m.add("LIMIT_HEAP", ClvmFlags::LIMIT_HEAP.bits())?;
    m.add("MEMPOOL_MODE", MEMPOOL_MODE.bits())?;
    m.add("ENABLE_SHA256_TREE", ClvmFlags::ENABLE_SHA256_TREE.bits())?;
    m.add("ENABLE_SECP_OPS", ClvmFlags::ENABLE_SECP_OPS.bits())?;
    m.add("DISABLE_OP", ClvmFlags::DISABLE_OP.bits())?;
    m.add("CANONICAL_INTS", ClvmFlags::CANONICAL_INTS.bits())?;
```
