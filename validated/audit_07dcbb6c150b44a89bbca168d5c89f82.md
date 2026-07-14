Let me read the full `apply_op` function and the `parse_softfork_arguments` interaction more carefully.

### Title
Mempool/Consensus Split via Unbounded Softfork Stack Depth in Consensus Mode — (`src/run_program.rs`, `src/chia_dialect.rs`)

### Summary

The softfork stack depth limit of 20 is enforced **only** when `ClvmFlags::LIMIT_SOFTFORK` is set (mempool mode). In consensus mode (`ClvmFlags::empty()`), there is no depth check at all. A CLVM program with 21 or more nested `softfork` invocations using `extension=0` is accepted by consensus but rejected by the mempool with `SoftforkStackDepthExceeded`, creating a concrete relay-breaking mempool/consensus split.

### Finding Description

The depth guard in `apply_op` is conditioned entirely on the `LIMIT_SOFTFORK` flag: [1](#0-0) 

```rust
if self.dialect.flags().contains(ClvmFlags::LIMIT_SOFTFORK)
    && self.softfork_stack.len() >= 20
{
    return Err(EvalErr::SoftforkStackDepthExceeded);
}
```

`LIMIT_SOFTFORK` is only present in `MEMPOOL_MODE`: [2](#0-1) 

In consensus mode (`ChiaDialect::new(ClvmFlags::empty())`), the flag is absent and the check is never reached. The `softfork_stack` grows without bound.

The softfork guard is entered for `extension=0` in **both** modes because `softfork_extension(0)` returns `OperatorSet::Bls` (not `OperatorSet::Default`), so `parse_softfork_arguments` succeeds and does not fall through to the consensus-mode no-op path: [3](#0-2) [4](#0-3) 

The consensus-mode no-op fallback (lines 403–409) only fires when `parse_softfork_arguments` returns an error (e.g., unknown extension ≥ 2). For `extension=0` it succeeds, so the guard is fully entered and `softfork_stack.push(...)` executes unconditionally: [5](#0-4) 

### Impact Explanation

A program with 21 nested `softfork` calls (each with `extension=0`, a valid cost argument, and a trivial inner program such as `(q . ())`) will:

- **Consensus mode**: pass the depth check (no check exists), enter all 21 guards, execute successfully, return nil.
- **Mempool mode**: on the 21st entry, `softfork_stack.len()` equals 20, the check fires, and `EvalErr::SoftforkStackDepthExceeded` is returned — the transaction is rejected.

This is a direct mempool/consensus split. Transactions included in blocks are valid on-chain but are rejected by mempool nodes, so they cannot be relayed. Full nodes in mempool mode will refuse to forward these transactions even though they are consensus-valid.

### Likelihood Explanation

The exploit requires only a crafted CLVM program with 21 nested softfork invocations — no special privileges, no compromised nodes, no social engineering. The program is trivially constructable and the split is deterministic. Any attacker who can submit a transaction to the network can trigger this.

### Recommendation

Remove the `LIMIT_SOFTFORK` condition from the depth check, or apply the same depth limit unconditionally in both modes. The depth limit should be a consensus rule, not a mempool-only policy:

```rust
// Apply in both modes:
if self.softfork_stack.len() >= 20 {
    return Err(EvalErr::SoftforkStackDepthExceeded);
}
```

Alternatively, if the limit is intentionally mempool-only, document and enforce that no consensus-valid program may exceed depth 20 by making the limit a hard consensus rule activated at a specific block height.

### Proof of Concept

```rust
// Pseudocode for a Rust unit test
// Build: (softfork cost 0 (softfork cost 0 (... 21 levels ... (q . ()) env) env) env)
// Run with ClvmFlags::empty()  -> Ok(...)
// Run with MEMPOOL_MODE        -> Err(SoftforkStackDepthExceeded)

let program = build_nested_softfork(&mut allocator, 21, extension=0);
let consensus = ChiaDialect::new(ClvmFlags::empty());
let mempool   = ChiaDialect::new(MEMPOOL_MODE);

assert!(run_program(&mut allocator, &consensus, program, env, u64::MAX).is_ok());
assert_eq!(
    run_program(&mut allocator, &mempool, program, env, u64::MAX),
    Err(EvalErr::SoftforkStackDepthExceeded)
);
```

The split is locally testable, deterministic, and directly maps to the code at [1](#0-0)  with the flag definition at [6](#0-5) .

### Citations

**File:** src/run_program.rs (L354-368)
```rust
    fn parse_softfork_arguments(&self, args: NodePtr) -> Result<(OperatorSet, NodePtr, NodePtr)> {
        let [_cost, extension, program, env] = get_args::<4>(self.allocator, args, "softfork")?;

        let extension = self.dialect.softfork_extension(uint_atom::<4>(
            self.allocator,
            extension,
            "softfork",
            self.dialect.flags(),
        )? as u32);
        if extension == OperatorSet::Default {
            Err(EvalErr::UnknownSoftforkExtension)
        } else {
            Ok((extension, program, env))
        }
    }
```

**File:** src/run_program.rs (L415-419)
```rust
            if self.dialect.flags().contains(ClvmFlags::LIMIT_SOFTFORK)
                && self.softfork_stack.len() >= 20
            {
                return Err(EvalErr::SoftforkStackDepthExceeded);
            }
```

**File:** src/run_program.rs (L421-427)
```rust
            self.softfork_stack.push(SoftforkGuard {
                expected_cost: current_cost + expected_cost,
                allocator_state: self.allocator.checkpoint(),
                operator_set: ext,
                #[cfg(test)]
                start_cost: current_cost,
            });
```

**File:** src/chia_dialect.rs (L44-45)
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

**File:** src/chia_dialect.rs (L269-276)
```rust
    fn softfork_extension(&self, ext: u32) -> OperatorSet {
        match ext {
            // Extension 0 is for the BLS operators, and is still valid.
            // However, the extension doesn't add any addition opcodes,
            // because the BLS operators were hardforked into the main set.
            0 => OperatorSet::Bls,

            // Extension 1 is for the keccak256 operator.
```
