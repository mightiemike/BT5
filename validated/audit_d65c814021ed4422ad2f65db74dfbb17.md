### Title
Underpaid `expected_cost` in `softfork` Operator Causes Permanent Coin Unspendability — (File: src/run_program.rs)

---

### Summary

The `softfork` operator in `clvm_rs` requires the puzzle author to embed an exact `expected_cost` value in the CLVM program bytes. If this value is set too low, the softfork program fails with `CostExceeded` inside the guard. After a softfork extension activates, any coin whose puzzle contains a `softfork` call with an underpaid `expected_cost` becomes permanently unspendable — an irreversible loss of funds. This is a direct analog to the Optimism `l2Gas` underpayment bug: in both cases, a user-controlled cost parameter that is too low causes a critical cross-boundary operation to fail irrecoverably.

---

### Finding Description

In `apply_op` (`src/run_program.rs`, lines 384–433), the `softfork` operator reads its first argument as the caller-declared `expected_cost`:

```rust
let expected_cost = uint_atom::<8>(
    self.allocator,
    first(self.allocator, operand_list)?,
    "softfork",
    self.dialect.flags(),
)?;
``` [1](#0-0) 

This value is then stored in a `SoftforkGuard` as the absolute cost ceiling for the program executing inside the guard:

```rust
self.softfork_stack.push(SoftforkGuard {
    expected_cost: current_cost + expected_cost,
    ...
});
``` [2](#0-1) 

During execution, the main loop uses `sf.expected_cost` as `effective_max_cost`:

```rust
let effective_max_cost = if let Some(sf) = self.softfork_stack.last() {
    sf.expected_cost
} else {
    max_cost
};
if cost > effective_max_cost {
    return Err(EvalErr::CostExceeded);
}
``` [3](#0-2) 

When the guard exits, `exit_guard` enforces exact equality:

```rust
if current_cost != guard.expected_cost {
    return Err(EvalErr::SoftforkCostMismatch);
}
``` [4](#0-3) 

There are therefore two failure modes for a wrong `expected_cost`:
- **Too low**: the program inside the guard hits `CostExceeded` mid-execution.
- **Too high**: `exit_guard` fires `SoftforkCostMismatch`.

Only an exactly correct value succeeds. The puzzle author must pre-compute the precise cost of the softfork sub-program and hard-code it into the puzzle bytes. There is no runtime mechanism to auto-correct or clamp the value.

**The consensus-mode bypass makes this asymmetric.** Before a softfork extension is known to a node, `parse_softfork_arguments` returns `Err(UnknownSoftforkExtension)`, and in consensus mode (`allow_unknown_ops() == true`) the interpreter silently accepts the call and charges `expected_cost` without executing the program:

```rust
if self.dialect.allow_unknown_ops() {
    self.push(self.allocator.nil())?;
    return Ok(expected_cost);
}
``` [5](#0-4) 

This means:
- **Before softfork activation**: old nodes accept any `expected_cost` (unknown extension path). The coin is spendable.
- **After softfork activation**: all nodes execute the program and enforce exact cost. If `expected_cost` is wrong, the spend is rejected by every node.

A coin whose puzzle contains `(softfork (q . N) (q . 1) <program> ())` with an incorrect `N` transitions from spendable to permanently unspendable at the moment the softfork extension activates — with no recovery path.

The `softfork_extension` dispatch in `ChiaDialect` confirms that extensions 0 (BLS) and 1 (Keccak) are live, making this a production-reachable path:

```rust
fn softfork_extension(&self, ext: u32) -> OperatorSet {
    match ext {
        0 => OperatorSet::Bls,
        1 => OperatorSet::Keccak,
        _ => OperatorSet::Default,
    }
}
``` [6](#0-5) 

---

### Impact Explanation

A coin whose puzzle embeds a `softfork` call with an underpaid `expected_cost` becomes permanently unspendable the moment the referenced softfork extension activates on-chain. The funds locked in that coin are irrecoverable. This is a direct loss-of-funds impact, not a transient failure: the coin's puzzle hash is fixed at creation time and cannot be patched after deployment.

The corrupted result is the `EvalErr::CostExceeded` (or `EvalErr::SoftforkCostMismatch`) returned from `run_program`, causing the spend bundle to be rejected by every full node after activation. [7](#0-6) 

---

### Likelihood Explanation

**Medium.** The `expected_cost` must be computed by the puzzle author by running the softfork sub-program through the interpreter and recording the exact cost. This is a manual, error-prone step. The cost model is sensitive to every operator, argument size, and tree depth. Any change to the sub-program after the cost was measured (e.g., a refactor, a different argument) silently invalidates the embedded cost. The codebase's own test suite demonstrates the one-unit sensitivity:

```
(softfork (q . 159) (q . 0) (q . (q . 42)) (q . ())) → CostExceeded  (actual cost is 160)
(softfork (q . 160) (q . 0) (q . (q . 42)) (q . ())) → OK
(softfork (q . 161) (q . 0) (q . (q . 42)) (q . ())) → SoftforkCostMismatch
``` [8](#0-7) 

Puzzle authors who develop and test their puzzles before a softfork activates will observe the coin as spendable (unknown-extension path), giving no warning that the embedded cost is wrong until activation.

---

### Recommendation

1. **Document the exact-cost requirement prominently** in the softfork operator documentation and in `docs/new-operator-checklist.md`. Warn that an incorrect `expected_cost` causes permanent coin unspendability after activation, not a recoverable error.
2. **Provide a cost-measurement utility** (or extend the existing `tools/` CLI) that runs a softfork sub-program and prints the exact cost to embed, reducing the chance of manual miscalculation.
3. **Consider adding a pre-flight check** in the Python bindings (`wheel/`) that warns when a `softfork` call's declared cost does not match the measured cost of the sub-program, surfacing the mismatch at puzzle-authoring time rather than at spend time.



---

### Proof of Concept

**Attacker-controlled entry path:** CLVM program bytes supplied as the puzzle of a coin.

**Trigger:** Embed a `softfork` call with `expected_cost` one unit below the actual sub-program cost.

```clvm
;; Actual cost of (q . 42) inside the guard = 160
;; Declared cost = 159  →  CostExceeded at activation
(softfork (q . 159) (q . 0) (q . (q . 42)) (q . ()))
```

**Before softfork extension 0/1 activates:** `parse_softfork_arguments` returns `UnknownSoftforkExtension`; consensus mode accepts the call and charges 159. Coin is spendable.

**After activation:** The sub-program is executed. At cost 160 the guard ceiling (159) is exceeded. `run_program` returns `Err(EvalErr::CostExceeded)`. Every node rejects the spend. The coin is permanently locked.

The broken invariant: `current_cost (160) > guard.expected_cost (current_cost_at_entry + 159)` triggers `CostExceeded` inside the loop at `src/run_program.rs:514–516`, before `exit_guard` is ever reached. [9](#0-8)

### Citations

**File:** src/run_program.rs (L385-390)
```rust
            let expected_cost = uint_atom::<8>(
                self.allocator,
                first(self.allocator, operand_list)?,
                "softfork",
                self.dialect.flags(),
            )?;
```

**File:** src/run_program.rs (L403-409)
```rust
                    if self.dialect.allow_unknown_ops() {
                        // In this case, we encountered a softfork invocation
                        // that doesn't pass the correct arguments.
                        // if we're in consensus mode, we have to accept this as
                        // something we don't understand
                        self.push(self.allocator.nil())?;
                        return Ok(expected_cost);
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

**File:** src/run_program.rs (L461-468)
```rust
        if current_cost != guard.expected_cost {
            #[cfg(test)]
            println!(
                "actual cost: {} specified cost: {}",
                current_cost - guard.start_cost,
                guard.expected_cost - guard.start_cost
            );
            return Err(EvalErr::SoftforkCostMismatch);
```

**File:** src/run_program.rs (L503-516)
```rust
        loop {
            // if we are in a softfork guard, temporarily use the guard's
            // expected cost as the upper limit. This lets us fail early in case
            // it's wrong. It's guaranteed to be <= max_cost, because we check
            // that when entering the softfork guard
            let effective_max_cost = if let Some(sf) = self.softfork_stack.last() {
                sf.expected_cost
            } else {
                max_cost
            };

            if cost > effective_max_cost {
                return Err(EvalErr::CostExceeded);
            }
```

**File:** src/run_program.rs (L1195-1222)
```rust
        // test mismatching cost
        RunProgramTest {
            prg: "(softfork (q . 160) (q . 0) (q . (q . 42)) (q . ()))",
            args: "()",
            flags: ClvmFlags::empty(),
            result: Some("()"),
            cost: 241,
            err: "",
        },
        // the program under the softfork is restricted by the specified cost
        RunProgramTest {
            prg: "(softfork (q . 159) (q . 0) (q . (q . 42)) (q . ()))",
            args: "()",
            flags: ClvmFlags::empty(),
            result: None,
            cost: 241,
            err: "cost exceeded or below zero",
        },
        // the cost specified on the softfork must match exactly the cost of
        // executing the program
        RunProgramTest {
            prg: "(softfork (q . 161) (q . 0) (q . (q . 42)) (q . ()))",
            args: "()",
            flags: ClvmFlags::empty(),
            result: None,
            cost: 10000,
            err: "softfork specified cost mismatch",
        },
```

**File:** src/chia_dialect.rs (L269-283)
```rust
    fn softfork_extension(&self, ext: u32) -> OperatorSet {
        match ext {
            // Extension 0 is for the BLS operators, and is still valid.
            // However, the extension doesn't add any addition opcodes,
            // because the BLS operators were hardforked into the main set.
            0 => OperatorSet::Bls,

            // Extension 1 is for the keccak256 operator.
            1 => OperatorSet::Keccak,

            // Extensions 2 and beyond are considered invalid by the mempool.
            // However, all future extensions are valid in consensus mode and reserved for future softforks.
            _ => OperatorSet::Default,
        }
    }
```

**File:** src/error.rs (L26-33)
```rust
    #[error("cost exceeded or below zero")]
    CostExceeded,

    #[error("unknown softfork extension")]
    UnknownSoftforkExtension,

    #[error("softfork specified cost mismatch")]
    SoftforkCostMismatch,
```
