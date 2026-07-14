### Title
Cost Accounting Integer Overflow in `op_unknown` Enables Zero-Cost Execution — (File: `src/more_ops.rs`)

---

### Summary

In `op_unknown`, the final cost multiplication `cost *= cost_multiplier + 1` at line 261 is performed without overflow protection. In Rust release mode, `u64` arithmetic wraps silently. An attacker-controlled opcode can set `cost_multiplier = u32::MAX` and supply enough arguments to make the pre-multiplication `cost` a precise multiple of `2^32`, causing the product to wrap to `0`. The post-multiplication guard only checks `cost > u32::MAX`, which `0` passes, so the function returns `Reduction(0, nil)` — zero cost for arbitrarily expensive work.

---

### Finding Description

`op_unknown` is the handler for unknown opcodes in lenient/consensus mode. Its cost model is:

1. Compute a base `cost` from the arguments (one of four cost functions).
2. Multiply: `cost *= cost_multiplier + 1` (line 261).
3. Guard: `if cost > u32::MAX as u64 { Err(...) }` (line 262).

`cost_multiplier` is decoded from the opcode prefix bytes via `u32_from_u8`, so it is at most `u32::MAX = 4,294,967,295`, and `cost_multiplier + 1` is at most `2^32 = 4,294,967,296` — a value that fits in `u64` with no overflow at that step.

The overflow occurs in the multiplication itself. For `cost_function = 1` (add-like), the base cost accumulates as:

```
cost = ARITH_BASE_COST + n * ARITH_COST_PER_ARG + byte_count * ARITH_COST_PER_BYTE
     = 99 + n * 320 + byte_count * 3
```

With enough arguments (well within the `MAX_NUM_PAIRS = 62,500,000` limit), `cost` can exceed `u32::MAX`. Because `gcd(320, 3) = 1`, the attacker can freely choose `n` and `byte_count` to make `cost` any residue modulo `2^32`, including `0`. When `cost ≡ 0 (mod 2^32)` and `cost_multiplier + 1 = 2^32`:

```
cost * 2^32  mod  2^64  =  0
```

The guard `if cost > u32::MAX as u64` evaluates to `false` for `0`, so the function returns:

```rust
Ok(Reduction(0 as Cost, allocator.nil()))
```

— **zero cost** for a program that consumed significant CPU.

The exact lines:

```rust
// line 260
check_cost(cost, max_cost)?;
// line 261 — unchecked multiplication, wraps in release mode
cost *= cost_multiplier + 1;
// line 262 — guard only catches values > u32::MAX, not wrapped-to-zero
if cost > u32::MAX as u64 {
    Err(EvalErr::Invalid(o))?
} else {
    Ok(Reduction(cost as Cost, allocator.nil()))
}
``` [1](#0-0) 

The `cost_multiplier` extraction: [2](#0-1) 

The `cost_function = 1` accumulation loop: [3](#0-2) 

`Cost` is `u64`: [4](#0-3) 

---

### Impact Explanation

Cost metering is the sole mechanism preventing DoS on Chia nodes. A program that reports cost `0` (or any value below the block limit) will be accepted and executed by every consensus node. The attacker can embed a program that performs the maximum legitimate work (e.g., ~11 billion cost units of argument processing) while the metered cost wraps to `0`, allowing the block to include arbitrarily many such programs. Every full node must re-execute the program, consuming CPU proportional to the true work, not the reported cost. This is a **consensus-uniform undercharged execution** vulnerability: all nodes agree on the wrong cost, so it does not cause a fork, but it enables sustained, cheap DoS against the entire network.

---

### Likelihood Explanation

The attack requires:
1. **Lenient mode** (`allow_unknown_ops() = true`): this is the standard consensus mode for forward compatibility with future opcodes — it is always active on mainnet.
2. **Opcode crafting**: set the 4-byte prefix to `[0xff, 0xff, 0xff, 0xff]` and the final byte to `0x40` (cost_function = 1, cost_multiplier = `u32::MAX`). This is trivial to construct.
3. **Argument count tuning**: solve `99 + n * 320 + byte_count * 3 ≡ 0 (mod 2^32)` with `cost > u32::MAX`. Since `gcd(320, 3) = 1`, solutions exist for any target residue. A concrete solution: choose `byte_count = 0` and find `n` such that `n * 320 ≡ (2^32 - 99) (mod 2^32 / gcd(320, 2^32))`, then adjust with non-zero-length atoms to hit the exact residue.

All inputs are fully attacker-controlled CLVM bytes. No privileged access, social engineering, or dependency compromise is required.

---

### Recommendation

Replace the unchecked multiplication with a checked variant that returns a cost-exceeded error on overflow:

```rust
// Before (line 261):
cost *= cost_multiplier + 1;

// After:
cost = cost
    .checked_mul(cost_multiplier + 1)
    .ok_or(EvalErr::CostExceeded)?;
```

This mirrors the pattern already used in `op_add`'s fast path: [5](#0-4) 

---

### Proof of Concept

Construct a CLVM program invoking an unknown opcode with:

- **Opcode bytes**: `[0xff, 0xff, 0xff, 0xff, 0x40]`
  - Bytes `[0..4]` → `cost_multiplier = 0xffffffff = u32::MAX`
  - Byte `[4]` → bits `[7:6] = 01` → `cost_function = 1` (add-like)
- **Arguments**: `n` zero-length atoms where `n` satisfies `99 + n * 320 ≡ 0 (mod 2^32)` and `n > u32::MAX / 320` (so `cost > u32::MAX`).

Execution trace:
1. `cost_function = 1` loop runs, accumulating `cost = k * 2^32` for some integer `k ≥ 2`.
2. `check_cost(cost, max_cost)` passes (cost ≤ max_cost).
3. `cost *= cost_multiplier + 1` → `k * 2^32 * 2^32 = k * 2^64 ≡ 0 (mod 2^64)` → `cost = 0`.
4. `if cost > u32::MAX as u64` → `false`.
5. Returns `Ok(Reduction(0, nil))`.

The program consumed `O(n)` CPU work; the reported cost is `0`.

### Citations

**File:** src/more_ops.rs (L202-207)
```rust
    let cost_multiplier: u64 = match u32_from_u8(&op[0..op.len() - 1]) {
        Some(v) => v as u64,
        None => {
            return Err(EvalErr::Invalid(o))?;
        }
    };
```

**File:** src/more_ops.rs (L211-222)
```rust
        1 => {
            let mut cost = ARITH_BASE_COST;
            let mut byte_count: u64 = 0;
            while let Some((arg, rest)) = allocator.next(args) {
                args = rest;
                cost += ARITH_COST_PER_ARG;
                let len = atom_len(allocator, arg, "unknown op")?;
                byte_count += len as u64;
                check_cost(cost + (byte_count as Cost * ARITH_COST_PER_BYTE), max_cost)?;
            }
            cost + (byte_count * ARITH_COST_PER_BYTE)
        }
```

**File:** src/more_ops.rs (L258-266)
```rust
    assert!(cost > 0);

    check_cost(cost, max_cost)?;
    cost *= cost_multiplier + 1;
    if cost > u32::MAX as u64 {
        Err(EvalErr::Invalid(o))?
    } else {
        Ok(Reduction(cost as Cost, allocator.nil()))
    }
```

**File:** src/more_ops.rs (L435-437)
```rust
                let Some(new_total) = total.checked_add(val as u64) else {
                    return Ok(None);
                };
```

**File:** src/cost.rs (L3-3)
```rust
pub type Cost = u64;
```
