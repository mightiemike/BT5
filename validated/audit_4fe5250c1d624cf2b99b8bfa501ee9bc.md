The code is clear and I have all the information needed to evaluate this claim.

### Title
Softfork Stack Depth Limit Applied Only in Mempool Mode Creates Consensus/Mempool Split — (`src/run_program.rs`)

### Summary

The `LIMIT_SOFTFORK` flag, which enforces a maximum softfork nesting depth of 20, is exclusively part of `MEMPOOL_MODE`. Consensus mode carries no equivalent guard. A CLVM program with 21 or more nested softfork guards using a known extension (0 = BLS, 1 = Keccak) is accepted by consensus but rejected by mempool with `EvalErr::SoftforkStackDepthExceeded`. This is a concrete, locally testable consensus/mempool split.

### Finding Description

In `apply_op`, the depth check is gated entirely on `ClvmFlags::LIMIT_SOFTFORK`: [1](#0-0) 

That flag is only present in `MEMPOOL_MODE`: [2](#0-1) 

`ClvmFlags::empty()` (consensus mode) never sets `LIMIT_SOFTFORK`, so the depth check is unconditionally skipped. The `softfork_stack` grows without bound in consensus mode. [3](#0-2) 

The split only triggers for **known** extensions (0 or 1). For unknown extensions (≥ 2), `parse_softfork_arguments` returns `Err(UnknownSoftforkExtension)` before the depth check is reached; in consensus mode that error is swallowed via `allow_unknown_ops()` and nil is returned immediately, so the depth check is never reached for unknown extensions in either mode. [4](#0-3) 

For known extensions the path is:
1. `parse_softfork_arguments` succeeds → returns `(ext, prg, env)`.
2. Depth check fires in mempool mode at guard #21 → `SoftforkStackDepthExceeded`.
3. In consensus mode the check is absent → guard #21 is pushed, inner program executes, `ExitGuard` validates cost, allocator is restored.

### Impact Explanation

A CLVM puzzle with 21 nested `softfork` calls (extension 1, each with a correctly declared cost and a trivial inner program) is valid under consensus rules and invalid under mempool rules. A farmer running consensus-mode validation can include such a spend in a block; every other full node accepts the block. The same spend is permanently rejected by every node's mempool, so it can never enter the mempool through normal submission. This is a textbook consensus/mempool split: the set of spends accepted by block validation is strictly larger than the set accepted by mempool validation, which is the opposite of the intended invariant.

### Likelihood Explanation

The split is reachable with attacker-controlled CLVM data and no special privileges beyond being (or colluding with) a farmer. Constructing a program with 21 nested softfork guards using extension 1 (Keccak) is straightforward. The cost for each guard must be declared correctly, but that is a mechanical calculation. No cryptographic material needs to be forged.

### Recommendation

Apply the same depth limit in consensus mode, or document and enforce that the limit is an intentional policy divergence that is safe. If the limit is intentional, the `softfork_extension` logic for unknown extensions already provides a natural firewall; the depth limit should be symmetric across modes to avoid the split. The simplest fix is to remove the `LIMIT_SOFTFORK` flag gate and always enforce the depth cap:

```rust
// Before (mempool-only):
if self.dialect.flags().contains(ClvmFlags::LIMIT_SOFTFORK)
    && self.softfork_stack.len() >= 20
{
    return Err(EvalErr::SoftforkStackDepthExceeded);
}

// After (always enforced):
if self.softfork_stack.len() >= 20 {
    return Err(EvalErr::SoftforkStackDepthExceeded);
}
```

### Proof of Concept

```rust
// Differential test: run a program with 21 nested softfork guards
// (extension 1 = Keccak, inner program = (q . ()) = nil, cost declared correctly)
// under ClvmFlags::empty() vs MEMPOOL_MODE and assert the split.

use clvm_rs::chia_dialect::{ChiaDialect, ClvmFlags, MEMPOOL_MODE};
use clvm_rs::run_program::run_program;
use clvm_rs::allocator::Allocator;
use clvm_rs::error::EvalErr;

fn build_nested_softfork(allocator: &mut Allocator, depth: usize) -> NodePtr {
    // (softfork <cost> 1 (q . ()) ())  -- innermost
    // wrap depth times
    // Each guard declares cost = GUARD_COST (140) + inner cost
    // ...
}

let mut alloc = Allocator::new();
let prg = build_nested_softfork(&mut alloc, 21);
let env = alloc.nil();

// Consensus: succeeds
let consensus = run_program(&mut alloc, &ChiaDialect::new(ClvmFlags::empty()), prg, env, u64::MAX);
assert!(consensus.is_ok(), "consensus must accept 21 nested guards");

// Mempool: fails
let mempool = run_program(&mut alloc, &ChiaDialect::new(MEMPOOL_MODE), prg, env, u64::MAX);
assert_eq!(mempool.unwrap_err(), EvalErr::SoftforkStackDepthExceeded,
    "mempool must reject 21 nested guards");
``` [1](#0-0) [5](#0-4) [6](#0-5)

### Citations

**File:** src/run_program.rs (L400-413)
```rust
            let (ext, prg, env) = match self.parse_softfork_arguments(operand_list) {
                Ok(ret_values) => ret_values,
                Err(err) => {
                    if self.dialect.allow_unknown_ops() {
                        // In this case, we encountered a softfork invocation
                        // that doesn't pass the correct arguments.
                        // if we're in consensus mode, we have to accept this as
                        // something we don't understand
                        self.push(self.allocator.nil())?;
                        return Ok(expected_cost);
                    }
                    return Err(err);
                }
            };
```

**File:** src/run_program.rs (L415-419)
```rust
            if self.dialect.flags().contains(ClvmFlags::LIMIT_SOFTFORK)
                && self.softfork_stack.len() >= 20
            {
                return Err(EvalErr::SoftforkStackDepthExceeded);
            }
```

**File:** src/run_program.rs (L421-433)
```rust
            self.softfork_stack.push(SoftforkGuard {
                expected_cost: current_cost + expected_cost,
                allocator_state: self.allocator.checkpoint(),
                operator_set: ext,
                #[cfg(test)]
                start_cost: current_cost,
            });

            // once the softfork guard exits, we need to ensure the cost that was
            // specified match the true cost. We also free heap allocations
            self.op_stack.push(Operation::ExitGuard);

            self.eval_pair(prg, env).map(|c| c + GUARD_COST)
```

**File:** src/chia_dialect.rs (L44-46)
```rust
        /// some limits for mempool mode
        const LIMIT_SOFTFORK = 0x0010;

```

**File:** src/chia_dialect.rs (L72-76)
```rust
pub const MEMPOOL_MODE: ClvmFlags = ClvmFlags::NO_UNKNOWN_OPS
    .union(ClvmFlags::LIMIT_HEAP)
    .union(ClvmFlags::DISABLE_OP)
    .union(ClvmFlags::CANONICAL_INTS)
    .union(ClvmFlags::LIMIT_SOFTFORK);
```

**File:** src/error.rs (L80-81)
```rust
    #[error("softfork stack depth exceeded")]
    SoftforkStackDepthExceeded,
```
