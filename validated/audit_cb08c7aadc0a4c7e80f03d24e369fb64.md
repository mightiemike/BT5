### Title
`op_unknown` Cost-Limit Check Applied Before Multiplier Scaling Allows Returned Cost to Exceed `max_cost` — (File: `src/more_ops.rs`)

---

### Summary

In `op_unknown`, the cost-limit guard (`check_cost`) is applied to the **pre-multiplied** base cost, but the value actually returned in the `Reduction` is the **post-multiplied** cost. This is a direct arithmetic semantic mismatch: the enforcement happens at one scale, while the value that propagates to the caller lives at a different, much larger scale. When the unknown op is the last operation on the execution stack, `run_program` exits its loop and returns `Ok(Reduction(inflated_cost, result))` without ever comparing the inflated cost against `max_cost`.

---

### Finding Description

In `src/more_ops.rs`, `op_unknown` computes a base cost from the opcode's `cost_function` field, then multiplies it by `(cost_multiplier + 1)` where `cost_multiplier` is derived from up to four leading bytes of the opcode atom (a `u32`, so up to `0xFFFFFFFF`).

The critical ordering is:

```
check_cost(cost, max_cost)?;   // line 260 — checks PRE-multiplied cost
cost *= cost_multiplier + 1;   // line 261 — scales AFTER the check
if cost > u32::MAX as u64 {    // line 262 — only guard is overflow, not max_cost
    Err(EvalErr::Invalid(o))?
} else {
    Ok(Reduction(cost as Cost, allocator.nil()))  // returns inflated cost
}
``` [1](#0-0) 

`check_cost` at line 260 sees only the base cost (e.g., `1` for `cost_function = 0`, or `ARITH_BASE_COST + per-arg/per-byte` for others). The multiplier is applied afterward. The only post-multiplication guard is `cost > u32::MAX as u64`, which caps the returned cost at `u32::MAX` (4,294,967,295) — not at `max_cost`.

In `run_program`, the main loop checks `cost > effective_max_cost` at the **top** of each iteration, before popping the next operation:

```rust
if cost > effective_max_cost {
    return Err(EvalErr::CostExceeded);
}
let top = self.op_stack.pop();
let op = match top {
    Some(f) => f,
    None => break,          // exits when stack is empty
};
cost += match op { ... };   // inflated cost added here
``` [2](#0-1) 

For a simple program such as `(unknown_op)`, the `Apply` operation that dispatches to `op_unknown` is the **last** entry on the op-stack. After `cost += inflated_cost`, the stack is empty, the loop breaks, and `run_program` returns:

```rust
Ok(Reduction(cost, self.pop()?))
``` [3](#0-2) 

There is no final check of `cost` against `max_cost` after the loop. The caller receives `Ok` with a cost that can be up to `u32::MAX`, regardless of what `max_cost` was.

The `cost_multiplier` is parsed by `u32_from_u8`, which accepts up to four bytes and returns values up to `0xFFFFFFFF`: [4](#0-3) 

Unknown ops are available in consensus (non-mempool) mode. `MEMPOOL_MODE` sets `NO_UNKNOWN_OPS`, which rejects them, but consensus-mode execution does not: [5](#0-4) 

---

### Impact Explanation

`run_program` is the sole cost enforcer. Callers pass `max_cost` and trust that an `Ok` return means the program executed within budget. When `op_unknown` returns a cost exceeding `max_cost` and that op is the last on the stack, `run_program` returns `Ok` with an inflated cost. This breaks the cost-accounting invariant: a program that should have been rejected as too expensive instead succeeds. In the Chia blockchain context this means a coin puzzle can execute and produce spend conditions while reporting a cost that exceeds the caller's budget, enabling undercharged execution and potential consensus divergence between nodes that enforce the limit differently.

---

### Likelihood Explanation

Unknown ops are reachable in consensus mode from any attacker-controlled CLVM program. Crafting the trigger requires only choosing an opcode byte sequence that:
- Does not start with `0xffff` (reserved prefix check at line 197)
- Has `cost_function = 0` in the top two bits of the last byte (simplest case, base cost = 1)
- Has a large multiplier in the preceding bytes [6](#0-5) 

A single-expression program suffices. No special privileges, no social engineering, and no dependency on external state are required.

---

### Recommendation

Move `check_cost` to **after** the multiplication, so the enforced value matches the value that is returned:

```rust
cost *= cost_multiplier + 1;
if cost > u32::MAX as u64 {
    Err(EvalErr::Invalid(o))?
} else {
    check_cost(cost as Cost, max_cost)?;   // enforce at the correct scale
    Ok(Reduction(cost as Cost, allocator.nil()))
}
```

This mirrors how every other operator in the file calls `check_cost` on the value it is about to return, not on an intermediate pre-scaled value.

---

### Proof of Concept

**Opcode construction** (cost_function = 0, multiplier = 2,000,000):

```
op bytes: [0x1E, 0x84, 0x80, 0x00]   (4 bytes)
  op[0..3] = [0x1E, 0x84, 0x80]  → u32_from_u8 = 0x1E8480 = 2,000,000
  op[3]    = 0x00                 → cost_function = (0x00 >> 6) & 0x3 = 0
```

**Execution trace** with `max_cost = 1,000,000`:

1. `op_unknown` receives `max_cost = 1,000,000`.
2. `cost_function = 0` → base `cost = 1`.
3. `check_cost(1, 1_000_000)` → passes.
4. `cost *= 2_000_000 + 1` → `cost = 2_000_001`.
5. `2_000_001 > u32::MAX` → false → returns `Reduction(2_000_001, nil)`.
6. Back in `run_program`: `cost += 2_000_001`; op-stack is now empty → `break`.
7. Returns `Ok(Reduction(2_000_001, nil))` — **cost exceeds `max_cost` of 1,000,000 with no error**. [1](#0-0) [7](#0-6)

### Citations

**File:** src/more_ops.rs (L197-207)
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

**File:** src/run_program.rs (L503-561)
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
    }
```

**File:** src/op_utils.rs (L137-158)
```rust
fn u32_from_u8_impl(buf: &[u8], signed: bool) -> Option<u32> {
    if buf.is_empty() {
        return Some(0);
    }

    // too many bytes for u32
    if buf.len() > 4 {
        return None;
    }

    let sign_extend = (buf[0] & 0x80) != 0;
    let mut ret: u32 = if signed && sign_extend { 0xffffffff } else { 0 };
    for b in buf {
        ret <<= 8;
        ret |= *b as u32;
    }
    Some(ret)
}

pub fn u32_from_u8(buf: &[u8]) -> Option<u32> {
    u32_from_u8_impl(buf, false)
}
```

**File:** src/chia_dialect.rs (L72-90)
```rust
pub const MEMPOOL_MODE: ClvmFlags = ClvmFlags::NO_UNKNOWN_OPS
    .union(ClvmFlags::LIMIT_HEAP)
    .union(ClvmFlags::DISABLE_OP)
    .union(ClvmFlags::CANONICAL_INTS)
    .union(ClvmFlags::LIMIT_SOFTFORK);

fn unknown_operator(
    allocator: &mut Allocator,
    o: NodePtr,
    args: NodePtr,
    flags: ClvmFlags,
    max_cost: Cost,
) -> Response {
    if flags.contains(ClvmFlags::NO_UNKNOWN_OPS) {
        Err(EvalErr::Unimplemented(o))?
    } else {
        op_unknown(allocator, o, args, max_cost)
    }
}
```
