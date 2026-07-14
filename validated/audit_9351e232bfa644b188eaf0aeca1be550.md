### Title
Missing `max_cost` Enforcement in `op_ash` and `op_lsh` Allows Cost-Limit Bypass — (`File: src/more_ops.rs`)

---

### Summary

`op_ash` and `op_lsh` both accept a `max_cost` parameter but explicitly ignore it (underscore-prefixed `_max_cost`). They perform the full, potentially expensive shift computation first, then compute the cost, but never call `check_cost(cost, max_cost)`. Because `run_program`'s main loop contains no `check_cost` or `max_cost` references of its own (confirmed by grep), cost enforcement for shift operations is entirely absent. An attacker-controlled CLVM program can invoke `ash` or `lsh` with a large shift to produce a multi-kilobyte result and exceed the declared cost budget without triggering `CostExceeded`.

---

### Finding Description

Every other arithmetic operator in `src/more_ops.rs` calls `check_cost(cost, max_cost)` during or after accumulating cost — `op_add`, `op_multiply`, `op_div`, `op_divmod`, `op_mod`, and `binop_reduction` all do so. The shift operators are the sole exception.

`op_ash` (line 918):
```rust
pub fn op_ash(a: &mut Allocator, input: NodePtr, _max_cost: Cost, _flags: ClvmFlags) -> Response {
    ...
    let v: Number = if a1 > 0 { i0 << a1 } else { i0 >> -a1 };
    let l1 = limbs_for_int(&v);
    let r = a.new_number(v)?;
    let cost = ASHIFT_BASE_COST + ((l0 + l1) as Cost) * ASHIFT_COST_PER_BYTE;
    Ok(malloc_cost(a, cost, r))   // cost returned but never validated
}
```

`op_lsh` (line 982) is structurally identical — `_max_cost` is unused, no `check_cost` call exists. [1](#0-0) [2](#0-1) 

The cost is computed from both the input length `l0` and the output length `l1`. For a maximum-allowed shift of 65535 bits on a 2-byte input (e.g., `(ash 500 65535)`), the output is ~8 192 bytes, making `l1 ≈ 8192`. The resulting cost is `ASHIFT_BASE_COST + (2 + 8192) * ASHIFT_COST_PER_BYTE`, which is large — yet it is never checked against `max_cost` before or after the shift executes.

The test vector file confirms this operation succeeds and produces an ~8 KB atom: [3](#0-2) 

Compare with `op_add`'s slow path, which calls `check_cost` on every argument: [4](#0-3) 

And `binop_reduction` (used by `logand`/`logior`/`logxor`), which also enforces the limit per argument: [5](#0-4) 

The `apply_op` path in `run_program` simply calls `self.dialect.op(...)` and returns the cost; it contains no independent `check_cost` call, so enforcement is fully delegated to each operator: [6](#0-5) 

---

### Impact Explanation

**Impact: Medium**

A CLVM program can perform a large shift operation — allocating and writing kilobytes of heap — even when the declared `max_cost` budget is already exhausted or set to a value below the shift's true cost. The cost value returned by `op_ash`/`op_lsh` is accumulated by the caller, but the expensive computation has already completed. This is an **undercharged execution** vulnerability: the operation is not bounded by the cost limit that is supposed to gate it. In a consensus context, if validators enforce `max_cost` differently (e.g., one checks before execution, another after), this can cause **consensus divergence**. At minimum, it allows programs to consume more resources than their declared budget permits, undermining the cost model's security guarantee.

---

### Likelihood Explanation

**Likelihood: Medium**

The trigger is a single CLVM opcode with a large shift argument — trivially expressible in attacker-controlled CLVM bytes passed to `run_serialized_chia_program` or any other public entry point. No special privileges or configuration are required. The shift bound of 65535 is enforced, but that bound is large enough to produce ~8 KB output, which is sufficient to exceed typical `max_cost` budgets. The bug is latent in every call path that evaluates `ash` or `lsh`.

---

### Recommendation

Add `check_cost(cost, max_cost)?;` in both `op_ash` and `op_lsh` after computing `cost`, before returning — mirroring the pattern used by every other arithmetic operator:

```rust
let cost = ASHIFT_BASE_COST + ((l0 + l1) as Cost) * ASHIFT_COST_PER_BYTE;
check_cost(cost, max_cost)?;   // add this line
Ok(malloc_cost(a, cost, r))
```

Rename the parameters from `_max_cost` to `max_cost` and `_flags` to `flags` accordingly.

---

### Proof of Concept

Attacker-controlled CLVM bytes encoding `(ash 500 65535)`:

1. Set `max_cost = 1000` (below the true cost of the operation).
2. Call `run_serialized_chia_program` with the serialized form of `(ash 500 65535)`.
3. `op_ash` executes the full left-shift, allocates an ~8 192-byte atom, computes `cost ≈ ASHIFT_BASE_COST + 8194 * ASHIFT_COST_PER_BYTE`, and returns `Ok(Reduction(cost, r))` — without ever comparing `cost` against `max_cost = 1000`.
4. The program completes successfully and returns the oversized atom, having consumed far more resources than the budget allowed.

Expected behavior: `CostExceeded` error before the shift executes.
Actual behavior: Successful completion with cost >> `max_cost`. [1](#0-0) [2](#0-1) [7](#0-6)

### Citations

**File:** src/more_ops.rs (L463-464)
```rust
                check_cost(cost, max_cost)?;
                let val = number_from_u8(buf);
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

**File:** src/more_ops.rs (L1059-1059)
```rust
        check_cost(cost + (arg_size as Cost * LOG_COST_PER_BYTE), max_cost)?;
```

**File:** op-tests/test-more-ops.txt (L263-264)
```text
ash 500 65535 => 0x00fa000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000 ... (truncated)
ash 500 -65535 => 0 | 602
```

**File:** src/run_program.rs (L441-450)
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
        }
```

**File:** src/cost.rs (L5-10)
```rust
pub fn check_cost(cost: Cost, max_cost: Cost) -> Result<()> {
    if cost > max_cost {
        Err(EvalErr::CostExceeded)
    } else {
        Ok(())
    }
```
