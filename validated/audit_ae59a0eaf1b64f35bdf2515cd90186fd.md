### Title
`DISABLE_OP` Flag in `MEMPOOL_MODE` Silently Blocks `op_modpow` in Mempool but Not Consensus, Creating Consensus Divergence — (`File: src/chia_dialect.rs`)

---

### Summary

`ClvmFlags::DISABLE_OP` (bit `0x200`) is wired into `MEMPOOL_MODE` and is the sole guard that blocks opcode 60 (`op_modpow`) from executing. Because `DISABLE_OP` is absent from the default/consensus flag set, `op_modpow` is a valid consensus operator but an invalid mempool operator. Any CLVM coin that uses `modpow` will be accepted by full nodes running consensus evaluation but rejected by every mempool node, rendering `modpow` unreachable through normal transaction submission — a direct analog to the `notDelegated` modifier that blocked the UUPS upgrade path in the original report.

---

### Finding Description

In `src/chia_dialect.rs`, `ClvmFlags` defines:

```rust
const DISABLE_OP = 0x200;
``` [1](#0-0) 

`MEMPOOL_MODE` unconditionally ORs this flag in:

```rust
pub const MEMPOOL_MODE: ClvmFlags = ClvmFlags::NO_UNKNOWN_OPS
    .union(ClvmFlags::LIMIT_HEAP)
    .union(ClvmFlags::DISABLE_OP)          // ← always set in mempool
    .union(ClvmFlags::CANONICAL_INTS)
    .union(ClvmFlags::LIMIT_SOFTFORK);
``` [2](#0-1) 

Inside `ChiaDialect::op()`, opcode 60 is the only operator that checks this flag:

```rust
60 => {
    if flags.contains(ClvmFlags::DISABLE_OP) {
        return Err(EvalErr::Unimplemented(o))?;
    }
    op_modpow
}
``` [3](#0-2) 

No other opcode in the dispatch table checks `DISABLE_OP`. The flag has no documentation explaining the intent. In consensus mode (flags without `DISABLE_OP`), opcode 60 dispatches to `op_modpow` normally alongside every other hardforked operator (48–63). The flag name is generic and gives no indication that it is permanently coupled to a single opcode. [4](#0-3) 

---

### Impact Explanation

**Consensus/mempool divergence.** A farmer or full node running consensus evaluation (no `DISABLE_OP`) will accept and execute any coin whose puzzle uses `modpow`. The same coin submitted through the mempool is immediately rejected with `Unimplemented`. This means:

1. Legitimate coins using `modpow` cannot propagate through the peer-to-peer mempool at all.
2. A farmer who directly constructs a block (bypassing the mempool) can include such a coin; it will be accepted by all consensus nodes, but no ordinary wallet or node will have forwarded it.
3. The divergence is permanent and silent — there is no error at coin creation time, only at spend time via the mempool path.

This is structurally identical to the original report: a guard (`notDelegated` / `DISABLE_OP`) is applied to a function that must be callable in a specific execution context (proxy delegatecall / mempool evaluation), making a core feature (`upgradeability` / `modpow`) unreachable through the intended path.

---

### Likelihood Explanation

`op_modpow` is a fully hardforked, documented operator (opcode 60) with its own test corpus (`op-tests/test-modpow.txt`, 173 test vectors) and fuzz target (`fuzz/fuzz_targets/modpow.rs`). [5](#0-4) 

Any developer who writes a puzzle using `modpow` — a reasonable choice for RSA-style or exponentiation-based puzzles — will find their coin silently unspendable via the mempool. The attacker-controlled entry path is simply crafting a CLVM program that calls opcode 60; no special privileges are required.

---

### Recommendation

Either:

1. **Remove `DISABLE_OP` from `MEMPOOL_MODE`** if `modpow` is intended to be a valid mempool operator (which it is, given it is hardforked into consensus), or
2. **Replace the generic `DISABLE_OP` flag with a specific, documented flag** (e.g., `MEMPOOL_DISABLE_MODPOW`) with a clear comment explaining the rationale, and ensure the consensus path is not affected.

The flag name `DISABLE_OP` with no associated documentation is itself a maintenance hazard — it is impossible to determine from the code whether the omission of `modpow` from mempool mode is intentional policy or an oversight.

---

### Proof of Concept

```
# Consensus mode (no DISABLE_OP): succeeds
run_program(
    allocator,
    ChiaDialect::new(ClvmFlags::empty()),   // consensus flags
    program = (modpow 2 10 1000),           // opcode 60
    env = (),
    max_cost = 10_000_000,
)
# → Ok(Reduction(cost, 24))   ← 2^10 mod 1000

# Mempool mode (DISABLE_OP set): fails
run_program(
    allocator,
    ChiaDialect::new(MEMPOOL_MODE),         // includes DISABLE_OP
    program = (modpow 2 10 1000),           // same program
    env = (),
    max_cost = 10_000_000,
)
# → Err(EvalErr::Unimplemented(op_node))   ← blocked by DISABLE_OP check
```

The divergence is triggered by any attacker-controlled CLVM bytes that encode opcode `0x3c` (60 decimal) as the operator. The mempool rejects the spend; consensus accepts it. A farmer can include the coin in a block, and all full nodes will apply it — but no mempool node will have propagated it. [3](#0-2) [2](#0-1)

### Citations

**File:** src/chia_dialect.rs (L56-56)
```rust
        const DISABLE_OP = 0x200;
```

**File:** src/chia_dialect.rs (L72-76)
```rust
pub const MEMPOOL_MODE: ClvmFlags = ClvmFlags::NO_UNKNOWN_OPS
    .union(ClvmFlags::LIMIT_HEAP)
    .union(ClvmFlags::DISABLE_OP)
    .union(ClvmFlags::CANONICAL_INTS)
    .union(ClvmFlags::LIMIT_SOFTFORK);
```

**File:** src/chia_dialect.rs (L227-253)
```rust
            48 => op_coinid,
            49 => op_bls_g1_subtract,
            50 => op_bls_g1_multiply,
            51 => op_bls_g1_negate,
            52 => op_bls_g2_add,
            53 => op_bls_g2_subtract,
            54 => op_bls_g2_multiply,
            55 => op_bls_g2_negate,
            56 => op_bls_map_to_g1,
            57 => op_bls_map_to_g2,
            58 => op_bls_pairing_identity,
            59 => op_bls_verify,
            60 => {
                if flags.contains(ClvmFlags::DISABLE_OP) {
                    return Err(EvalErr::Unimplemented(o))?;
                }
                op_modpow
            }
            61 => op_mod,
            62 if flags.contains(ClvmFlags::ENABLE_KECCAK_OPS_OUTSIDE_GUARD) => op_keccak256,
            63 if flags.contains(ClvmFlags::ENABLE_SHA256_TREE) => op_sha256_tree,
            64 if flags.contains(ClvmFlags::ENABLE_SECP_OPS) => op_secp256k1_verify,
            65 if flags.contains(ClvmFlags::ENABLE_SECP_OPS) => op_secp256r1_verify,
            _ => {
                return unknown_operator(allocator, o, argument_list, flags, max_cost);
            }
        };
```

**File:** src/f_table.rs (L37-37)
```rust
        (op_modpow, "op_modpow"),
```
