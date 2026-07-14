### Title
`DISABLE_OP` Flag Check Is Dead Code in `op_div`, `op_divmod`, and `op_mod` — Mempool Protection Offers No Additional Restriction - (File: src/more_ops.rs)

### Summary

In `op_div`, `op_divmod`, and `op_mod` (and their malachite variants), the `DISABLE_OP` flag is supposed to enforce a stricter operand-size limit in mempool mode. However, the flag-gated threshold (2048 bytes) is larger than the unconditional hard limit (256 bytes), making the `DISABLE_OP` check permanently dead code. The flag provides zero additional protection for these three operators, directly analogous to using `block.timestamp` as a swap deadline: a protection parameter whose value is always already satisfied by a stricter unconditional constraint.

### Finding Description

`MEMPOOL_MODE` includes `ClvmFlags::DISABLE_OP` as one of its constituent flags, signalling that this flag is intended to add stricter restrictions during mempool validation. [1](#0-0) 

For `op_modpow` (opcode 60), `DISABLE_OP` correctly and completely disables the operator: [2](#0-1) 

But for `op_div`, `op_divmod`, and `op_mod`, the flag is used differently — it is supposed to enforce a stricter size limit on the first argument. In all six affected functions (three operators × two bigint backends), the pattern is identical:

```rust
// DISABLE_OP check — threshold 2048
if flags.contains(ClvmFlags::DISABLE_OP) && a0_len > 2048 {
    return Err(EvalErr::InvalidOpArg(...));
}
// Unconditional hard limit — threshold 256
if a0_len > 256 || a1_len > 1024 {
    return Err(EvalErr::InvalidOpArg(...));
}
```

Because `256 < 2048`, any input that would trigger the `DISABLE_OP` branch (`a0_len > 2048`) is **already rejected** by the unconditional check (`a0_len > 256`) that immediately follows. The flag-gated check is unreachable for all attacker-supplied inputs.

Affected locations:

- `op_div` (num-bigint path): [3](#0-2) 
- `op_div_malachite`: [4](#0-3) 
- `op_divmod` (num-bigint path): [5](#0-4) 
- `op_divmod_malachite`: [6](#0-5) 
- `op_mod` (num-bigint path): [7](#0-6) 
- `op_mod_malachite`: [8](#0-7) 

### Impact Explanation

`MEMPOOL_MODE` is the stricter validation mode used before a transaction is admitted to the mempool. The `DISABLE_OP` flag is explicitly part of this mode and is expected to enforce tighter operand-size limits on `op_div`, `op_divmod`, and `op_mod`. Because the flag check is dead code, mempool-mode validation for these three operators is **identical** to consensus-mode validation. Any transaction whose first argument to `/`, `divmod`, or `mod` has a byte length in the range `(intended_stricter_limit, 256]` is accepted by the mempool when it should be rejected. This weakens the mempool's role as a spam/DoS filter and means the `DISABLE_OP` flag's stated protection for these operators is entirely illusory — the same structural defect as setting a swap deadline to `block.timestamp`.

### Likelihood Explanation

The entry path is direct and requires only attacker-controlled CLVM bytes. Any caller that submits a serialized program containing `/`, `divmod`, or `mod` with a first argument between the intended stricter threshold and 256 bytes exercises this path. No special privileges, social engineering, or configuration changes are required. The `DISABLE_OP` flag is wired into `MEMPOOL_MODE` and is therefore active on every full node running mempool validation.

### Recommendation

The `DISABLE_OP` threshold for `op_div`, `op_divmod`, and `op_mod` must be set to a value **strictly less than** the unconditional hard limit of 256 bytes to be meaningful. If the intent is to completely disable these operators in mempool mode (consistent with how `op_modpow` is handled), the check should be:

```rust
if flags.contains(ClvmFlags::DISABLE_OP) {
    return Err(EvalErr::Unimplemented(input));
}
```

If the intent is a stricter size limit (e.g., 128 bytes), the threshold must be corrected to that value. The same fix must be applied to all six affected functions (both bigint backends for each of the three operators).

### Proof of Concept

Submit the following CLVM program in mempool mode (`MEMPOOL_MODE` flags):

```
(/ (q . <257-byte integer>) (q . 2))
```

A 257-byte integer has `a0_len = 257 > 256`, so it is rejected by the unconditional check — this is expected. Now submit:

```
(/ (q . <256-byte integer>) (q . 2))
```

`a0_len = 256`. The `DISABLE_OP` check (`a0_len > 2048`) is false, so it is skipped. The unconditional check (`a0_len > 256`) is also false (256 is not > 256), so execution proceeds. The program is accepted in mempool mode. If the intended stricter limit under `DISABLE_OP` was, for example, 128 bytes, then any first argument with `129 ≤ a0_len ≤ 256` bypasses the intended mempool restriction entirely, demonstrating that the `DISABLE_OP` flag provides no protection for these operators.

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

**File:** src/chia_dialect.rs (L240-244)
```rust
                if flags.contains(ClvmFlags::DISABLE_OP) {
                    return Err(EvalErr::Unimplemented(o))?;
                }
                op_modpow
            }
```

**File:** src/more_ops.rs (L665-669)
```rust
    if flags.contains(ClvmFlags::DISABLE_OP) && a0_len > 2048 {
        return Err(EvalErr::InvalidOpArg(input, "div".to_string()));
    }
    if a0_len > 256 || a1_len > 1024 {
        return Err(EvalErr::InvalidOpArg(input, "div".to_string()));
```

**File:** src/more_ops.rs (L690-694)
```rust
    if flags.contains(ClvmFlags::DISABLE_OP) && a0_len > 2048 {
        return Err(EvalErr::InvalidOpArg(input, "div".to_string()));
    }
    if a0_len > 256 || a1_len > 1024 {
        return Err(EvalErr::InvalidOpArg(input, "div".to_string()));
```

**File:** src/more_ops.rs (L713-717)
```rust
    if flags.contains(ClvmFlags::DISABLE_OP) && a0_len > 2048 {
        return Err(EvalErr::InvalidOpArg(input, "divmod".to_string()));
    }
    if a0_len > 256 || a1_len > 1024 {
        return Err(EvalErr::InvalidOpArg(input, "divmod".to_string()));
```

**File:** src/more_ops.rs (L742-746)
```rust
    if flags.contains(ClvmFlags::DISABLE_OP) && a0_len > 2048 {
        return Err(EvalErr::InvalidOpArg(input, "divmod".to_string()));
    }
    if a0_len > 256 || a1_len > 1024 {
        return Err(EvalErr::InvalidOpArg(input, "divmod".to_string()));
```

**File:** src/more_ops.rs (L769-773)
```rust
    if flags.contains(ClvmFlags::DISABLE_OP) && a0_len > 2048 {
        return Err(EvalErr::InvalidOpArg(input, "mod".to_string()));
    }
    if a0_len > 256 || a1_len > 1024 {
        return Err(EvalErr::InvalidOpArg(input, "mod".to_string()));
```

**File:** src/more_ops.rs (L794-798)
```rust
    if flags.contains(ClvmFlags::DISABLE_OP) && a0_len > 2048 {
        return Err(EvalErr::InvalidOpArg(input, "mod".to_string()));
    }
    if a0_len > 256 || a1_len > 1024 {
        return Err(EvalErr::InvalidOpArg(input, "mod".to_string()));
```
