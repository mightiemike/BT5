### Title
Integer Division Rounding in Multiplication Cost Undercharges Execution Cost — (`src/more_ops.rs`)

---

### Summary

In `op_multiply` (and the `cost_function == 2` branch of `op_unknown`), the quadratic component of the multiplication cost is computed using integer division:

```rust
cost += (l0 as Cost * l1) / MUL_SQUARE_COST_PER_BYTE_DIVIDER;
```

where `MUL_SQUARE_COST_PER_BYTE_DIVIDER = 128`. Because Rust integer division truncates toward zero, the quadratic cost term is systematically rounded down — and for operand sizes below ~12 bytes, it is rounded to **zero entirely**. This mirrors the external report's root cause: integer division silently discards a remainder, causing a resource-accounting value to be smaller than intended on every invocation.

---

### Finding Description

`MUL_SQUARE_COST_PER_BYTE_DIVIDER` is defined as `128` and is used to scale the quadratic (O(n²)) component of big-integer multiplication cost. [1](#0-0) 

The quadratic term appears in three places inside `op_multiply`: [2](#0-1) [3](#0-2) [4](#0-3) 

And once inside the `cost_function == 2` branch of `op_unknown`: [5](#0-4) 

Because `l0` and `l1` are byte-lengths of the operands, the product `l0 * l1` must reach **128** before any quadratic cost is charged at all. For operands up to 11 bytes (88-bit integers), `l0 * l1 ≤ 121 < 128`, so the entire quadratic term evaluates to **0**. For larger operands the rounding error is up to **127 cost units per multiplication step**.

The rounding is not a one-time event: it recurs on every pairwise multiplication in a multi-argument `*` call, and on every argument pair in an unknown-op with `cost_function == 2`.

---

### Impact Explanation

The cost model is the sole resource-limiting mechanism in CLVM. Undercharging the quadratic component of `op_multiply` allows an attacker to execute programs whose true computational cost exceeds what the cost model reports. Concretely:

- For 11-byte × 11-byte multiplications, the quadratic cost is **completely zeroed out** (0 instead of ≈0.95 cost units). The attacker pays only the linear component.
- For larger operands the per-step undercharge is up to 127 cost units. With a Chia block `max_cost` of 11 billion, a program consisting entirely of multiplications can accumulate an undercharge of roughly **1–1.5 billion cost units** (~12–14% of the budget), allowing proportionally more computation than the limit intends.
- The undercharge is deterministic and identical on every node, so there is no consensus divergence — but the cost ceiling is effectively lower than the protocol intends, weakening the DoS protection that the cost model provides.

---

### Likelihood Explanation

The trigger is entirely attacker-controlled CLVM bytes. Any caller of `run_program` (Chia full node, light wallet, Python wheel) that accepts externally supplied programs is exposed. Crafting a program that maximises the rounding loss requires only choosing operand byte-lengths such that `l0 * l1 mod 128` is maximised (e.g., 11-byte operands give remainder 121 out of 127 possible). No special privileges, keys, or social engineering are required.

---

### Recommendation

Replace the truncating integer division with ceiling division for the quadratic cost term:

```rust
// instead of:
cost += (l0 as Cost * l1) / MUL_SQUARE_COST_PER_BYTE_DIVIDER;
// use:
cost += (l0 as Cost * l1).div_ceil(MUL_SQUARE_COST_PER_BYTE_DIVIDER);
```

`div_ceil` is available on all integer primitives in Rust 1.73+. Apply the same fix to all three sites in `op_multiply` and the one site in `op_unknown`.

---

### Proof of Concept

Consider a CLVM program that multiplies many 11-byte integers together.

- Each pairwise step: `l0 = 11`, `l1 = 11`
- Quadratic term charged: `(11 × 11) / 128 = 121 / 128 = 0`
- Quadratic term that *should* be charged: `⌈121 / 128⌉ = 1`
- Undercharge per step: **1 cost unit** (100% of the quadratic component)

With `max_cost = 11_000_000_000` and a per-step base cost of `MUL_COST_PER_OP + linear ≈ 885 + 132 = 1017`, an attacker can execute approximately `10.8 million` multiplication steps. The total missing quadratic cost is `10.8M × 1 = 10.8M` cost units — equivalent to running roughly **10,000 additional free multiplication steps** beyond what the budget should permit. [6](#0-5) [7](#0-6)

### Citations

**File:** src/more_ops.rs (L34-37)
```rust
const MUL_BASE_COST: Cost = 92;
const MUL_COST_PER_OP: Cost = 885;
const MUL_LINEAR_COST_PER_BYTE: Cost = 6;
const MUL_SQUARE_COST_PER_BYTE_DIVIDER: Cost = 128;
```

**File:** src/more_ops.rs (L237-239)
```rust
                cost += (l0 + l1) * MUL_LINEAR_COST_PER_BYTE;
                cost += (l0 * l1) / MUL_SQUARE_COST_PER_BYTE_DIVIDER;
                l0 += l1;
```

**File:** src/more_ops.rs (L586-616)
```rust
pub fn op_multiply(
    a: &mut Allocator,
    mut input: NodePtr,
    max_cost: Cost,
    _flags: ClvmFlags,
) -> Response {
    let mut cost: Cost = MUL_BASE_COST;
    let mut first_iter: bool = true;
    let mut total: Number = 1.into();
    let mut l0: usize = 0;
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

        cost += MUL_COST_PER_OP;
        #[cfg(not(feature = "no-fastpath"))]
        match a.node(arg) {
            NodeVisitor::Buffer(buf) => {
                let l1 = buf.len() as u64;
                if l1 > 256 {
                    return Err(EvalErr::InvalidOpArg(arg, "*".to_string()));
                }
                cost += (l0 as Cost + l1) * MUL_LINEAR_COST_PER_BYTE;
                cost += (l0 as Cost * l1) / MUL_SQUARE_COST_PER_BYTE_DIVIDER;
```

**File:** src/more_ops.rs (L623-624)
```rust
                cost += (l0 as Cost + l1) * MUL_LINEAR_COST_PER_BYTE;
                cost += (l0 as Cost * l1) / MUL_SQUARE_COST_PER_BYTE_DIVIDER;
```

**File:** src/more_ops.rs (L643-644)
```rust
            cost += (l0 as Cost + l1) * MUL_LINEAR_COST_PER_BYTE;
            cost += (l0 as Cost * l1) / MUL_SQUARE_COST_PER_BYTE_DIVIDER;
```
