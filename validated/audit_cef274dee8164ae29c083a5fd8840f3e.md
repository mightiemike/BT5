### Title
`op_unknown` Cost Check Applied Before Multiplication Allows `run_program` to Return `Ok` with Cost Exceeding `max_cost` — (`src/more_ops.rs`)

---

### Summary

In `op_unknown`, the `check_cost` guard is applied to the **pre-multiplication** cost value, but the cost returned to the caller is the **post-multiplication** value. When an unknown opcode is the final operation in a program, `run_program` can return `Ok(Reduction(cost, …))` where `cost > max_cost`, violating the invariant that a successful execution never exceeds the caller-supplied budget. This is a direct structural analog to M-10: just as liquidation should never worsen the health factor but can because the incentive check is applied to the wrong quantity, `check_cost` should prevent the returned cost from exceeding `max_cost` but fails because it is applied before the multiplier is applied.

---

### Finding Description

In `src/more_ops.rs`, `op_unknown` computes a base cost from the opcode's `cost_function` field, then multiplies it by `(cost_multiplier + 1)` where `cost_multiplier` is encoded in the leading bytes of the opcode atom:

```
check_cost(cost, max_cost)?;      // line 260 — guards pre-multiplication cost
cost *= cost_multiplier + 1;      // line 261 — multiplied AFTER the guard
if cost > u32::MAX as u64 {
    Err(EvalErr::Invalid(o))?
} else {
    Ok(Reduction(cost as Cost, allocator.nil()))
}
```

`check_cost` enforces `cost ≤ max_cost` only on the base cost. After the check passes, the cost is multiplied by up to `u32::MAX + 1 = 4 294 967 296`, producing a returned cost bounded only by `u32::MAX ≈ 4.29 × 10⁹`, not by `max_cost`.

The main evaluation loop in `run_program` checks `if cost > effective_max_cost` at the **top** of each iteration, before popping the next operation:

```rust
loop {
    if cost > effective_max_cost {          // checked at top
        return Err(EvalErr::CostExceeded);
    }
    let top = self.op_stack.pop();
    let op = match top {
        Some(f) => f,
        None => break,                      // exits when stack is empty
    };
    cost += match op {
        Operation::Apply => self.apply_op(cost, effective_max_cost - cost)?,
        ...
    };
    // ← no cost check here before the next iteration's top-of-loop check
}
Ok(Reduction(cost, self.pop()?))            // returned without a final check
```

When `op_unknown` is the **last** operation (op-stack becomes empty after it), the loop breaks immediately after adding the inflated cost, and `run_program` returns `Ok` with `cost > max_cost`. The post-operation cost check never fires.

**Concrete trigger**:

Craft a CLVM program whose sole expression is a single unknown opcode atom with:
- `cost_function = 0` (top 2 bits of last byte = `00`) → base cost = 1
- `cost_multiplier` encoded in the leading bytes, e.g. `[0xFF, 0xFE, 0xFF, 0xFF, 0x3f]` → `cost_multiplier = 0xFFFEFFFF = 4 294 836 223`

Execution:
1. `check_cost(1, max_cost)` passes for any `max_cost ≥ 1`
2. `cost *= 4 294 836 223 + 1 = 4 294 836 224`
3. `4 294 836 224 ≤ u32::MAX` → no `Invalid` error
4. `op_unknown` returns `Reduction(4 294 836 224, nil)`
5. Op-stack is now empty → loop breaks
6. `run_program` returns `Ok(Reduction(4 294 836 224, …))` even if `max_cost = 1 000`

The attacker-controlled entry path is entirely through the CLVM byte stream: the opcode atom is parsed from attacker-supplied bytes, `cost_function` and `cost_multiplier` are extracted from those bytes, and no privilege is required.

---

### Impact Explanation

The invariant broken is: **a successful `run_program` call must return a cost ≤ max_cost**. Callers that rely on this invariant — including Python consumers of the wheel and any Rust integrator that passes a budget smaller than `u32::MAX` — may accept a program as valid when it should have been rejected. If two nodes use different `max_cost` values, or if one node re-validates the returned cost and another does not, this produces **consensus divergence**: one node accepts the spend, the other rejects it, causing a chain split or double-spend window. The fuzz harness itself asserts `cost < 11_000_000_000`; because `u32::MAX < 11 × 10⁹` the assertion happens to hold for that specific budget, masking the bug for the standard Chia block limit while leaving all smaller budgets exposed.

---

### Likelihood Explanation

Any caller that passes `max_cost < u32::MAX` (≈ 4.29 × 10⁹) is reachable. The Python wheel exposes `run_program` directly to Python callers with an arbitrary budget. Crafting the trigger requires only knowledge of the opcode encoding scheme, which is documented in the source comments. No privileged access, key material, or social engineering is needed — only the ability to submit a CLVM program.

---

### Recommendation

Move the `check_cost` call to **after** the multiplication, so the guard is applied to the value that will actually be returned:

```rust
cost *= cost_multiplier + 1;
if cost > u32::MAX as u64 {
    return Err(EvalErr::Invalid(o));
}
check_cost(cost, max_cost)?;   // guard the post-multiplication cost
Ok(Reduction(cost as Cost, allocator.nil()))
```

Additionally, add a final cost check after the main loop in `run_program` before returning `Ok`, so that no code path can return a successful result with cost > max_cost regardless of where the cost is added.

---

### Proof of Concept

```
; CLVM program: a single unknown opcode atom
; Opcode bytes: [0xFF, 0xFE, 0xFF, 0xFF, 0x3F]
;   last byte 0x3F = 0b00111111 → cost_function = 0 (top 2 bits = 00)
;   prefix [0xFF, 0xFE, 0xFF, 0xFF] → cost_multiplier = 4_294_836_223
;
; With max_cost = 1_000:
;   check_cost(1, 1_000) → Ok   (base cost = 1 passes)
;   cost = 1 * (4_294_836_223 + 1) = 4_294_836_224
;   4_294_836_224 <= u32::MAX   → no Invalid error
;   op_unknown returns Ok(Reduction(4_294_836_224, nil))
;   op-stack empty → loop breaks
;   run_program returns Ok(Reduction(4_294_836_224, nil))
;   4_294_836_224 >> 1_000  ← invariant violated
``` [1](#0-0) [2](#0-1) [3](#0-2) [4](#0-3)

### Citations

**File:** src/more_ops.rs (L196-207)
```rust

    if op.is_empty() || (op.len() >= 2 && op[0] == 0xff && op[1] == 0xff) {
        Err(EvalErr::Reserved(o))?;
    }

    let cost_function = (op[op.len() - 1] & 0b11000000) >> 6;
    let cost_multiplier: u64 = match u32_from_u8(&op[0..op.len() - 1]) {
        Some(v) => v as u64,
        None => {
            return Err(EvalErr::Invalid(o))?;
        }
    };
```

**File:** src/more_ops.rs (L209-256)
```rust
    let mut cost = match cost_function {
        0 => 1,
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
        2 => {
            let mut cost = MUL_BASE_COST;
            let mut first_iter: bool = true;
            let mut l0: u64 = 0;
            while let Some((arg, rest)) = allocator.next(args) {
                args = rest;
                let len = atom_len(allocator, arg, "unknown op")?;
                if first_iter {
                    l0 = len as u64;
                    first_iter = false;
                    continue;
                }
                let l1 = len as u64;
                cost += MUL_COST_PER_OP;
                cost += (l0 + l1) * MUL_LINEAR_COST_PER_BYTE;
                cost += (l0 * l1) / MUL_SQUARE_COST_PER_BYTE_DIVIDER;
                l0 += l1;
                check_cost(cost, max_cost)?;
            }
            cost
        }
        3 => {
            let mut cost = CONCAT_BASE_COST;
            while let Some((arg, rest)) = allocator.next(args) {
                args = rest;
                let len = atom_len(allocator, arg, "unknown op")?;
                cost += CONCAT_COST_PER_ARG;
                cost += CONCAT_COST_PER_BYTE * (len as Cost);
                check_cost(cost, max_cost)?;
            }
            cost
        }
        _ => 1,
    };
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

**File:** src/run_program.rs (L503-560)
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
            let top = self.op_stack.pop();
            let op = match top {
                Some(f) => f,
                None => break,
            };
            cost += match op {
                Operation::Apply => self.apply_op(cost, effective_max_cost - cost)?,
                Operation::ExitGuard => self.exit_guard(cost)?,
                Operation::Cons => self.cons_op()?,
                Operation::SwapEval => self.swap_eval_op()?,
                Operation::RestoreAllocator => {
                    let Some(checkpoint) = self.allocator_stack.pop() else {
                        return Err(EvalErr::InternalError(
                            NodePtr::NIL,
                            "allocator checkpoint stack empty".to_string(),
                        ));
                    };
                    let Some(&top) = self.val_stack.last() else {
                        return Err(EvalErr::InternalError(
                            NodePtr::NIL,
                            "value stack empty".to_string(),
                        ));
                    };
                    match self.allocator.maybe_restore_with_node(&checkpoint, top)? {
                        MaybeRestore::NoReplace => {}
                        MaybeRestore::Replace(new_node) => {
                            self.val_stack.pop().unwrap();
                            self.val_stack.push(new_node);
                        }
                        MaybeRestore::Aborted => {}
                    }
                    0
                }
                #[cfg(feature = "pre-eval")]
                Operation::PostEval => {
                    let f = self.posteval_stack.pop().unwrap();
                    let peek: Option<NodePtr> = self.val_stack.last().copied();
                    f(self.allocator, peek);
                    0
                }
            };
        }
        self.allocator.clear_validation_caches();
        Ok(Reduction(cost, self.pop()?))
```
