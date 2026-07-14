### Title
Missing `check_cost` Guard in `op_ash` and `op_lsh` Enables Undercharged Execution — (File: `src/more_ops.rs`)

---

### Summary

Both `op_ash` and `op_lsh` accept a `max_cost` parameter but deliberately ignore it (underscore-prefixed `_max_cost`). The expensive bignum shift computation and heap allocation occur unconditionally before any cost validation. The cost is computed only *after* the result is produced, and `check_cost` is never called within either function. This is the direct analog of the reported bug class: work is performed before the cost gate is applied, allowing an attacker to force nodes to execute expensive computations that exceed the remaining cost budget.

---

### Finding Description

In `src/more_ops.rs`, `op_ash` and `op_lsh` both follow this pattern:

```rust
pub fn op_ash(a: &mut Allocator, input: NodePtr, _max_cost: Cost, _flags: ClvmFlags) -> Response {
    let [n0, n1] = get_args::<2>(a, input, "ash")?;
    let (i0, l0) = int_atom(a, n0, "ash")?;
    let a1 = i32_atom(a, n1, "ash")?;
    if !(-65535..=65535).contains(&a1) {
        return Err(EvalErr::ShiftTooLarge(n1));
    }
    // ← expensive bignum shift happens here, unconditionally
    let v: Number = if a1 > 0 { i0 << a1 } else { i0 >> -a1 };
    let l1 = limbs_for_int(&v);
    let r = a.new_number(v)?;                          // ← heap allocation
    let cost = ASHIFT_BASE_COST + ((l0 + l1) as Cost) * ASHIFT_COST_PER_BYTE;
    // ← cost is computed AFTER the work; check_cost is never called
    Ok(malloc_cost(a, cost, r))
}
``` [1](#0-0) 

`op_lsh` has the identical structure: [2](#0-1) 

The `_max_cost` underscore prefix is a Rust convention confirming the parameter is intentionally unused. Neither function ever calls `check_cost`. [3](#0-2) 

Contrast this with `op_modpow`, which calls `check_cost` *before* the expensive `base.modpow()` computation: [4](#0-3) 

And `op_concat`, which calls `check_cost` inside the accumulation loop: [5](#0-4) 

The outer `run_program` loop passes `effective_max_cost - cost` as the `max_cost` argument to each operator via `apply_op`: [6](#0-5) 

This remaining-budget value is computed correctly and passed in, but `op_ash`/`op_lsh` discard it entirely. The outer loop only checks the returned cost *after* the operator has already returned: [7](#0-6) 

---

### Impact Explanation

An attacker can craft a CLVM program such as `(ash <large_atom> 65535)` or `(lsh <large_atom> 65535)`. When the remaining cost budget is, say, 100 units, the outer loop does not reject the call before dispatching to `op_ash`/`op_lsh` (the pre-dispatch check only fires if `cost > effective_max_cost` at the *start* of the iteration, not if the operator's cost *would* exceed it). The operator then:

1. Performs a bignum left-shift of up to 65535 bits on an arbitrarily large input atom.
2. Allocates the result on the heap (potentially thousands of bytes).
3. Computes and returns the true cost.

The outer loop then rejects the program with `CostExceeded` — but only after the expensive computation has already been performed. Because failed programs are not included in Chia blocks, the attacker pays no fees. This is a repeatable, zero-cost DoS vector against full nodes.

The maximum output size of a single `ash`/`lsh` call is bounded by `input_size + 65535/8 ≈ input_size + 8192 bytes`, but there is no explicit input size cap in either function (unlike `op_multiply` which caps operands at 256 bytes). [8](#0-7) 

---

### Likelihood Explanation

**Medium.** The attacker-controlled entry path is direct: submit a CLVM spend bundle containing `(ash ...)` or `(lsh ...)` with a large atom and near-maximum shift. The mempool accepts programs for evaluation before block inclusion. The computation is bounded (not unbounded), but the pattern can be repeated across many connections or spend bundles. The missing `check_cost` is not a design choice — it is inconsistent with every other expensive operator in the codebase.

---

### Recommendation

Add `check_cost` calls in `op_ash` and `op_lsh` before the expensive shift computation, mirroring the pattern used in `op_modpow` and `op_concat`. At minimum, a pre-computation upper-bound check should be inserted:

```rust
// Before performing the shift:
let upper_bound_cost = ASHIFT_BASE_COST + ((l0 as Cost) + (l0 as Cost + (a1.unsigned_abs() as Cost / 8 + 1))) * ASHIFT_COST_PER_BYTE;
check_cost(upper_bound_cost, max_cost)?;
```

Or, after computing `l1` (output size) but before allocating:

```rust
let l1 = limbs_for_int(&v);
let cost = ASHIFT_BASE_COST + ((l0 + l1) as Cost) * ASHIFT_COST_PER_BYTE;
check_cost(cost, max_cost)?;   // ← add this before a.new_number(v)
let r = a.new_number(v)?;
Ok(malloc_cost(a, cost, r))
```

The same fix applies symmetrically to `op_lsh`. [9](#0-8) 

---

### Proof of Concept

Attacker-controlled CLVM bytes (hex-encoded program):

```
; (ash (q . <256-byte-atom>) (q . 65535))
; Remaining cost budget: 100
```

Execution trace:
1. `run_program` dispatches `op_ash` with `max_cost = 100`.
2. `op_ash` ignores `max_cost`, shifts a 256-byte bignum left by 65535 bits.
3. Result is ~8448 bytes; allocated on the heap.
4. Returned cost ≈ `596 + (256 + 8448) * 3 = 26708`.
5. Outer loop: `cost += 26708 > 100` → `CostExceeded` returned.
6. Node has performed the full bignum shift and heap allocation for free. [1](#0-0) [2](#0-1)

### Citations

**File:** src/more_ops.rs (L596-605)
```rust
    while let Some((arg, rest)) = a.next(input) {
        input = rest;
        if first_iter {
            (total, l0) = int_atom(a, arg, "*")?;
            if l0 > 256 {
                return Err(EvalErr::InvalidOpArg(arg, "*".to_string()));
            }
            first_iter = false;
            continue;
        }
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

**File:** src/more_ops.rs (L1256-1264)
```rust
    let mut cost = MODPOW_BASE_COST;
    let (base, bsize) = int_atom(a, base, "modpow")?;
    cost += bsize as Cost * MODPOW_COST_PER_BYTE_BASE_VALUE;
    let (exponent, esize) = int_atom(a, exponent, "modpow")?;
    cost += (esize * esize) as Cost * MODPOW_COST_PER_BYTE_EXPONENT;
    check_cost(cost, max_cost)?;
    let (modulus, msize) = int_atom(a, modulus, "modpow")?;
    cost += (msize * msize) as Cost * MODPOW_COST_PER_BYTE_MOD;
    check_cost(cost, max_cost)?;
```

**File:** src/run_program.rs (L514-516)
```rust
            if cost > effective_max_cost {
                return Err(EvalErr::CostExceeded);
            }
```

**File:** src/run_program.rs (L522-523)
```rust
            cost += match op {
                Operation::Apply => self.apply_op(cost, effective_max_cost - cost)?,
```
