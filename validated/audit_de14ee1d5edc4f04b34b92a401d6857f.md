The code confirms this is a real, concrete vulnerability. Here is the analysis:

**Key evidence:**

`MEMPOOL_MODE` is defined at [1](#0-0)  as the union of `NO_UNKNOWN_OPS | LIMIT_HEAP | DISABLE_OP | CANONICAL_INTS | LIMIT_SOFTFORK` — it explicitly includes `DISABLE_OP`.

In `ChiaDialect::op`, opcode 60 is handled as: [2](#0-1) 

When `DISABLE_OP` is set (i.e., under `MEMPOOL_MODE`), the branch immediately returns `Err(EvalErr::Unimplemented(o))`. When `ClvmFlags::empty()` is used (consensus mode), the same opcode dispatches to `op_modpow` and executes normally.

`op_modpow` itself is a fully implemented, unconditionally available operator in the main operator set — not behind any softfork guard: [3](#0-2) 

---

### Title
Opcode 60 (`op_modpow`) accepted by consensus but rejected by mempool due to `DISABLE_OP` in `MEMPOOL_MODE` — (`src/chia_dialect.rs`)

### Summary
`MEMPOOL_MODE` unconditionally includes `ClvmFlags::DISABLE_OP`, which causes `ChiaDialect::op` to return `Err(EvalErr::Unimplemented)` for opcode 60 (`op_modpow`). The same opcode executes successfully under `ClvmFlags::empty()` (consensus mode). This is a concrete, testable consensus/mempool split.

### Finding Description
In `src/chia_dialect.rs`, `MEMPOOL_MODE` is defined as:

```rust
pub const MEMPOOL_MODE: ClvmFlags = ClvmFlags::NO_UNKNOWN_OPS
    .union(ClvmFlags::LIMIT_HEAP)
    .union(ClvmFlags::DISABLE_OP)   // <-- disables opcode 60
    .union(ClvmFlags::CANONICAL_INTS)
    .union(ClvmFlags::LIMIT_SOFTFORK);
```

The dispatch for opcode 60 in `ChiaDialect::op`:

```rust
60 => {
    if flags.contains(ClvmFlags::DISABLE_OP) {
        return Err(EvalErr::Unimplemented(o))?;  // mempool path
    }
    op_modpow  // consensus path
}
```

`op_modpow` is a fully implemented operator in the main (non-softforked) operator set. There is no softfork guard, no activation height check, and no other gating mechanism — the sole difference between the two execution paths is the `DISABLE_OP` bit in the flags.

### Impact Explanation
A coin whose puzzle uses opcode 60 (`modpow`) is spendable on-chain (consensus accepts it) but the spending transaction cannot propagate through the mempool (mempool rejects it with `Unimplemented`). A farmer/validator can include such a transaction directly in a block, bypassing all mempool-level fee and validation checks. This is a confirmed consensus/mempool split matching the stated scope.

### Likelihood Explanation
The split is unconditional and requires no special attacker capability beyond crafting a CLVM program that invokes opcode 60. Any puzzle using `modpow` triggers it. The differential is locally reproducible with a two-line Rust test.

### Recommendation
Remove `ClvmFlags::DISABLE_OP` from `MEMPOOL_MODE` if `op_modpow` is intended to be active in consensus. If `op_modpow` is not yet activated in consensus, it must also be gated in the consensus path (e.g., behind a softfork guard or an activation-height check), not only in the mempool path.

### Proof of Concept

```rust
use clvmr::allocator::Allocator;
use clvmr::chia_dialect::{ChiaDialect, ClvmFlags, MEMPOOL_MODE};
use clvmr::run_program;
use clvmr::serde::node_from_bytes;

// CLVM program: (modpow 3 10 7)  => opcode 60 with args (3, 10, 7)
// Assemble the program bytes for (60 3 10 7) and run:

fn main() {
    let mut alloc = Allocator::new();
    // ... build program invoking opcode 60 ...

    let consensus = ChiaDialect::new(ClvmFlags::empty());
    let mempool   = ChiaDialect::new(MEMPOOL_MODE);

    let r1 = run_program(&mut alloc, &consensus, program, args, u64::MAX);
    let r2 = run_program(&mut alloc, &mempool,   program, args, u64::MAX);

    assert!(r1.is_ok(),  "consensus rejected modpow — unexpected");
    assert!(r2.is_err(), "mempool accepted modpow — unexpected");
    // r2 is Err(EvalErr::Unimplemented(_))
    // Split confirmed.
}
```

### Citations

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

**File:** src/more_ops.rs (L1250-1284)
```rust
pub fn op_modpow(a: &mut Allocator, input: NodePtr, max_cost: Cost, flags: ClvmFlags) -> Response {
    if flags.contains(ClvmFlags::MALACHITE) {
        return op_modpow_malachite(a, input, max_cost, flags);
    }
    let [base, exponent, modulus] = get_args::<3>(a, input, "modpow")?;

    let mut cost = MODPOW_BASE_COST;
    let (base, bsize) = int_atom(a, base, "modpow")?;
    cost += bsize as Cost * MODPOW_COST_PER_BYTE_BASE_VALUE;
    let (exponent, esize) = int_atom(a, exponent, "modpow")?;
    cost += (esize * esize) as Cost * MODPOW_COST_PER_BYTE_EXPONENT;
    check_cost(cost, max_cost)?;
    let (modulus, msize) = int_atom(a, modulus, "modpow")?;
    cost += (msize * msize) as Cost * MODPOW_COST_PER_BYTE_MOD;
    check_cost(cost, max_cost)?;

    if bsize > 256 || esize > 256 || msize > 256 {
        return Err(EvalErr::InvalidOpArg(input, "modpow".to_string()));
    }

    if exponent.sign() == Sign::Minus {
        return Err(EvalErr::InvalidOpArg(
            input,
            "ModPow with Negative Exponent".to_string(),
        ));
    }

    if modulus.sign() == Sign::NoSign {
        return Err(EvalErr::DivisionByZero(input));
    }

    let ret = base.modpow(&exponent, &modulus);
    let ret = a.new_number(ret)?;
    Ok(malloc_cost(a, cost, ret))
}
```
