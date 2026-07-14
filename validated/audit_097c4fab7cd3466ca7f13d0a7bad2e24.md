### Title
`DISABLE_OP` Flag Wiring Error Completely Disables `modpow` in Mempool Mode, Creating Consensus Divergence - (File: `src/chia_dialect.rs`)

### Summary
The `DISABLE_OP` flag (0x200), always set in `MEMPOOL_MODE`, is wired in the opcode dispatch table to completely disable `op_modpow` (opcode 60) in mempool mode. In consensus mode (no `DISABLE_OP`), the same opcode executes normally. This creates a mempool-consensus divergence: any attacker-controlled CLVM program invoking opcode 60 is accepted by consensus but unconditionally rejected by every mempool node.

### Finding Description

In `src/chia_dialect.rs`, the `MEMPOOL_MODE` constant always includes `DISABLE_OP`: [1](#0-0) 

The opcode dispatch for opcode 60 (`modpow`) reads: [2](#0-1) 

When `DISABLE_OP` is set (i.e., in every mempool evaluation), the function immediately returns `EvalErr::Unimplemented` — the same error returned for unknown operators — before `op_modpow` is ever reached. In consensus mode, where `DISABLE_OP` is absent, opcode 60 dispatches to `op_modpow` and executes normally.

This is a flag/operator wiring error directly analogous to the reported inverted-condition bug class. The `DISABLE_OP` flag is used in three other operators (`op_div`, `op_divmod`, `op_mod`) only to add an *extra size restriction* (`a0_len > 2048`), not to completely disable the operator: [3](#0-2) [4](#0-3) 

For `modpow`, the same flag is wired to a total disable — a qualitatively different and far more severe action. The flag has no doc comment (unlike every other `ClvmFlags` member), which is consistent with it being misapplied here: [5](#0-4) 

All other conditionally-activated operators (`keccak256`, `sha256_tree`, `secp256k1_verify`, `secp256r1_verify`) use affirmative `ENABLE_*` flags in match guards: [6](#0-5) 

`modpow` is the only fully-dispatched operator that uses a negating `DISABLE_*` flag to suppress itself in the dispatch table.

### Impact Explanation

**Severity: High**

Any CLVM spend bundle that invokes opcode 60 (`modpow`) is valid on-chain (consensus mode accepts it) but is unconditionally rejected by every mempool node running `MEMPOOL_MODE`. The concrete corrupted result is `EvalErr::Unimplemented` returned from `ChiaDialect::op()` for opcode 60 under `MEMPOOL_MODE`, where the correct result is a successful `Reduction` from `op_modpow`. This is a mempool-consensus divergence: the mempool and the chain disagree on the validity of the same transaction, which can prevent valid spends from propagating and can be exploited to selectively censor `modpow`-using coins.

### Likelihood Explanation

**Likelihood: Medium**

`modpow` is a fully activated, documented operator with its own test file (`op-tests/test-modpow.txt`) and benchmark entries. Any Chialisp puzzle that uses `modpow` (e.g., for cryptographic proof verification) will trigger this divergence. The attacker entry path requires only crafting a CLVM program that calls opcode 60 with valid arguments — no special privileges or configuration needed.

### Recommendation

Remove the `DISABLE_OP` branch from the opcode-60 dispatch arm in `src/chia_dialect.rs`. If a mempool-specific size restriction on `modpow` inputs is desired (consistent with how `DISABLE_OP` is used in `op_div`/`op_divmod`/`op_mod`), add the analogous `a0_len > 2048` guard inside `op_modpow` itself rather than suppressing the entire operator at the dispatch level.

### Proof of Concept

```
; CLVM program invoking modpow (opcode 60)
; In consensus mode (no DISABLE_OP): returns 8 (2^3 mod 10)
; In mempool mode (DISABLE_OP set): returns EvalErr::Unimplemented

(modpow (q . 2) (q . 3) (q . 10))
```

Calling `run_program` with `ChiaDialect::new(MEMPOOL_MODE)` on this program returns `Unimplemented` for opcode 60. Calling it with `ChiaDialect::new(ClvmFlags::empty())` returns `Reduction(cost, 8)`. The divergence is triggered by any attacker-supplied CLVM bytes that encode opcode 60 as the operator atom.

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

**File:** src/chia_dialect.rs (L239-244)
```rust
            60 => {
                if flags.contains(ClvmFlags::DISABLE_OP) {
                    return Err(EvalErr::Unimplemented(o))?;
                }
                op_modpow
            }
```

**File:** src/chia_dialect.rs (L246-249)
```rust
            62 if flags.contains(ClvmFlags::ENABLE_KECCAK_OPS_OUTSIDE_GUARD) => op_keccak256,
            63 if flags.contains(ClvmFlags::ENABLE_SHA256_TREE) => op_sha256_tree,
            64 if flags.contains(ClvmFlags::ENABLE_SECP_OPS) => op_secp256k1_verify,
            65 if flags.contains(ClvmFlags::ENABLE_SECP_OPS) => op_secp256r1_verify,
```

**File:** src/more_ops.rs (L665-667)
```rust
    if flags.contains(ClvmFlags::DISABLE_OP) && a0_len > 2048 {
        return Err(EvalErr::InvalidOpArg(input, "div".to_string()));
    }
```

**File:** src/more_ops.rs (L713-715)
```rust
    if flags.contains(ClvmFlags::DISABLE_OP) && a0_len > 2048 {
        return Err(EvalErr::InvalidOpArg(input, "divmod".to_string()));
    }
```
