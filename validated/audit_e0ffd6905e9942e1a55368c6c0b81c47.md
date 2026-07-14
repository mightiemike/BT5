### Title
`op_ash` and `op_lsh` Ignore `max_cost`, Performing Expensive Computation Before Any Cost Validation — (`File: src/more_ops.rs`)

### Summary

Both `op_ash` (arithmetic shift) and `op_lsh` (logical shift) accept a `max_cost` parameter but explicitly discard it (parameter named `_max_cost`). They perform the full bignum shift and heap allocation before computing the cost, and never call `check_cost`. The cost limit is only enforced by the outer `run_program` loop on the **next** iteration, after the expensive work has already been done. An attacker-controlled CLVM program can force nodes to execute large shift operations that exceed the remaining cost budget, doing unbounded-per-call work without pre-validation.

### Finding Description

Every other cost-bounded operator in `src/more_ops.rs` follows the pattern of computing the cost first and calling `check_cost(cost, max_cost)?` **before** performing the expensive operation. For example, `op_div` computes the cost from argument sizes and validates it before calling `div_floor`:

```rust
let cost = DIV_BASE_COST + ((a0_len + a1_len) as Cost) * DIV_COST_PER_BYTE;
check_cost(cost, max_cost)?;   // ← validates BEFORE the division
let q = a0.div_floor(&a1);
``` [1](#0-0) 

`op_concat` similarly calls `check_cost` inside its loop before accumulating bytes:

```rust
cost += CONCAT_COST_PER_ARG;
cost += len as Cost * (CONCAT_COST_PER_BYTE + MALLOC_COST_PER_BYTE);
check_cost(cost, max_cost)?;   // ← validates incrementally
``` [2](#0-1) 

`op_ash` and `op_lsh` break this invariant entirely. Both signatures mark `max_cost` as unused:

```rust
pub fn op_ash(a: &mut Allocator, input: NodePtr, _max_cost: Cost, _flags: ClvmFlags) -> Response
pub fn op_lsh(a: &mut Allocator, input: NodePtr, _max_cost: Cost, _flags: ClvmFlags) -> Response
``` [3](#0-2) [4](#0-3) 

Both functions:
1. Parse arguments
2. Perform the full bignum shift (up to 65,535 bits, producing up to ~8,192 bytes)
3. Allocate the result in the `Allocator`
4. **Only then** compute the cost as `BASE_COST + (l0 + l1) * COST_PER_BYTE`
5. Return without ever calling `check_cost`

The outer `run_program` loop does check `cost > effective_max_cost`, but only at the **top** of the next iteration, after `cost += apply_op(...)` has already returned:

```rust
if cost > effective_max_cost {
    return Err(EvalErr::CostExceeded);
}
// ...
cost += match op {
    Operation::Apply => self.apply_op(cost, effective_max_cost - cost)?,
    // ...
};
// ← cost check deferred to next loop iteration
``` [5](#0-4) 

The `max_cost` passed into `apply_op` is `effective_max_cost - cost` (the remaining budget), and this is forwarded to the operator via `self.dialect.op(...)`: [6](#0-5) 

But `op_ash`/`op_lsh` discard it, so the remaining budget is never consulted before the shift executes.

### Impact Explanation

An attacker who controls CLVM program bytes can include `ash` or `lsh` instructions with large operands (e.g., a 256-byte value shifted by 65,535 bits, producing an ~8,192-byte result). Even if the remaining cost budget is 0 or 1, the full bignum shift and heap allocation execute before the cost check fires. The maximum per-call overshoot is bounded by the 65,535-bit shift limit (result ≤ ~8,192 bytes, cost ≤ ~24,900 units), but this overshoot occurs on every such call in the program. A program consisting of many chained `ash`/`lsh` calls with large inputs forces the node to perform significantly more work than the declared cost budget permits.

This is an **undercharged execution** issue: the cost limit is passed in but not enforced at the point of work, exactly analogous to the Pyth report where `msg.value` is passed but not validated against the required fee before the call executes.

**Impact:** Medium — bounded per-call overshoot, but directly attacker-controlled and repeatable across many operator invocations in a single program.

### Likelihood Explanation

**Likelihood:** Medium — any CLVM program submitted to a Chia full node or mempool validator is attacker-controlled. Crafting a program with large-shift `ash`/`lsh` calls requires no special privileges. The 65,535-bit shift cap limits the per-call damage but does not eliminate the structural bypass of the cost gate.

### Recommendation

Apply the same pre-execution cost validation pattern used by `op_div`, `op_concat`, and other operators. Compute the maximum possible cost before performing the shift and allocation, then call `check_cost`:

```rust
pub fn op_ash(a: &mut Allocator, input: NodePtr, max_cost: Cost, _flags: ClvmFlags) -> Response {
    let [n0, n1] = get_args::<2>(a, input, "ash")?;
    let (i0, l0) = int_atom(a, n0, "ash")?;
    let a1 = i32_atom(a, n1, "ash")?;
    if !(-65535..=65535).contains(&a1) {
        return Err(EvalErr::ShiftTooLarge(n1));
    }
    // Estimate worst-case output size before doing the work
    let max_l1 = l0 + (a1.unsigned_abs() as usize).div_ceil(8);
    let cost = ASHIFT_BASE_COST + ((l0 + max_l1) as Cost) * ASHIFT_COST_PER_BYTE;
    check_cost(cost, max_cost)?;   // ← validate before the expensive shift
    let v: Number = if a1 > 0 { i0 << a1 } else { i0 >> -a1 };
    let l1 = limbs_for_int(&v);
    let r = a.new_number(v)?;
    let cost = ASHIFT_BASE_COST + ((l0 + l1) as Cost) * ASHIFT_COST_PER_BYTE;
    Ok(malloc_cost(a, cost, r))
}
```

Apply the same fix to `op_lsh`. The cost constants are already defined: [7](#0-6) 

### Proof of Concept

```
; Program: (ash <256-byte-atom> (q . 65535))
; With max_cost = 1 (budget exhausted)
;
; op_ash ignores max_cost=1, performs the full 65535-bit left shift on a
; 256-byte input, allocates an ~8192-byte result in the Allocator, then
; returns cost ≈ 596 + (256 + 8192) * 3 ≈ 25,940.
; The outer loop adds this to cost and only then checks cost > max_cost,
; by which time the expensive allocation has already occurred.
;
; Repeat N times in a single program to force N * ~8192 bytes of allocation
; and N bignum shifts beyond the declared cost budget.
```

The attacker entry path is: submit a CLVM puzzle/solution pair to a Chia full node or mempool. The node calls `run_program` → `apply_op` → `dialect.op` → `op_ash`/`op_lsh` with the remaining cost budget, which the operator ignores. [8](#0-7) [9](#0-8)

### Citations

**File:** src/more_ops.rs (L62-66)
```rust
const ASHIFT_BASE_COST: Cost = 596;
const ASHIFT_COST_PER_BYTE: Cost = 3;

const LSHIFT_BASE_COST: Cost = 277;
const LSHIFT_COST_PER_BYTE: Cost = 3;
```

**File:** src/more_ops.rs (L671-678)
```rust
    let cost = DIV_BASE_COST + ((a0_len + a1_len) as Cost) * DIV_COST_PER_BYTE;
    check_cost(cost, max_cost)?;
    if a1.sign() == Sign::NoSign {
        return Err(EvalErr::DivisionByZero(input));
    }
    let q = a0.div_floor(&a1);
    let q = a.new_number(q)?;
    Ok(malloc_cost(a, cost, q))
```

**File:** src/more_ops.rs (L904-906)
```rust
        cost += CONCAT_COST_PER_ARG;
        cost += len as Cost * (CONCAT_COST_PER_BYTE + MALLOC_COST_PER_BYTE);
        check_cost(cost, max_cost)?;
```

**File:** src/more_ops.rs (L918-931)
```rust
pub fn op_ash(a: &mut Allocator, input: NodePtr, _max_cost: Cost, _flags: ClvmFlags) -> Response {
    let [n0, n1] = get_args::<2>(a, input, "ash")?;
    let (i0, l0) = int_atom(a, n0, "ash")?;
    let a1 = i32_atom(a, n1, "ash")?;
    if !(-65535..=65535).contains(&a1) {
        return Err(EvalErr::ShiftTooLarge(n1));
    }

    let v: Number = if a1 > 0 { i0 << a1 } else { i0 >> -a1 };
    let l1 = limbs_for_int(&v);
    let r = a.new_number(v)?;
    let cost = ASHIFT_BASE_COST + ((l0 + l1) as Cost) * ASHIFT_COST_PER_BYTE;
    Ok(malloc_cost(a, cost, r))
}
```

**File:** src/more_ops.rs (L982-1000)
```rust
pub fn op_lsh(a: &mut Allocator, input: NodePtr, _max_cost: Cost, _flags: ClvmFlags) -> Response {
    let [n0, n1] = get_args::<2>(a, input, "lsh")?;
    let b0_atom = atom(a, n0, "lsh")?;
    let b0 = b0_atom.as_ref();
    let a1 = i32_atom(a, n1, "lsh")?;
    if !(-65535..=65535).contains(&a1) {
        return Err(EvalErr::ShiftTooLarge(n1));
    }
    let i0 = BigUint::from_bytes_be(b0);
    let l0 = b0.len();
    let i0: Number = i0.into();

    let v: Number = if a1 > 0 { i0 << a1 } else { i0 >> -a1 };

    let l1 = limbs_for_int(&v);
    let r = a.new_number(v)?;
    let cost = LSHIFT_BASE_COST + ((l0 + l1) as Cost) * LSHIFT_COST_PER_BYTE;
    Ok(malloc_cost(a, cost, r))
}
```

**File:** src/run_program.rs (L441-449)
```rust
            let r = self.dialect.op(
                self.allocator,
                operator,
                operand_list,
                max_cost,
                current_extensions,
            )?;
            self.push(r.1)?;
            Ok(r.0)
```

**File:** src/run_program.rs (L514-524)
```rust
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
```
