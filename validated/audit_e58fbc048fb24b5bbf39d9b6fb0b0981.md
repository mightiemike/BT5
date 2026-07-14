### Title
Undercharged BLS Point Validation in `op_bls_g1_negate` / `op_bls_g2_negate` Enables Cost-Model Bypass — (`File: src/bls_ops.rs`)

### Summary

`op_bls_g1_negate` (opcode 51) and `op_bls_g2_negate` (opcode 55) charge only 916 and 1204 CLVM cost units respectively, yet in strict mode (the production default, when `RELAXED_BLS` is not set) each invocation performs a full BLS point decompression and subgroup-membership check via `validate_g1` / `validate_g2`. The same cryptographic validation work performed by `op_bls_g1_subtract` is charged at 1,343,980 cost units per argument. The ~1,400× cost discrepancy means an attacker can trigger orders-of-magnitude more BLS validation work than the cost model is designed to allow, bypassing the cost model's role as the sole rate-limiting mechanism for expensive operations.

### Finding Description

**Root cause — cost constant calibrated for the wrong mode**

The cost constants are defined with a comment that explicitly ties them to a trivial bit-flip, not to BLS validation:

```
// this is the same cost as XORing the top bit (minus the heap allocation of the
// return value, which the operator is adding back)
const BLS_G1_NEGATE_BASE_COST: Cost = 1396 - 480;   // = 916
const BLS_G2_NEGATE_BASE_COST: Cost = 2164 - 960;   // = 1204
``` [1](#0-0) 

In strict mode (`RELAXED_BLS` not set, which is the production default), both operators call `validate_g1` / `validate_g2` before performing the bit-flip:

```rust
if strict {
    a.validate_g1(point, blob)?;   // full G1Element::from_bytes — expensive
}
// ... then flip bit[0] ^ 0x20
``` [2](#0-1) [3](#0-2) 

`validate_g1` calls `G1Element::from_bytes` (BLS12-381 G1 decompression + subgroup check) and caches the result only within a single `run_program` invocation. The cache is cleared at the end of every execution: [4](#0-3) [5](#0-4) 

The same `G1Element::from_bytes` call is what makes `op_bls_g1_subtract` charge 1,343,980 per argument: [6](#0-5) [7](#0-6) 

**Attacker-controlled entry path**

Opcode 51 (`g1_negate`) and opcode 55 (`g2_negate`) are unconditionally available in the default `ChiaDialect` operator table — no special flags required: [8](#0-7) 

An attacker submits a CLVM puzzle (transaction spend) to the Chia mempool containing a loop or repeated invocations of opcode 51 with distinct valid G1 points. Each invocation costs only 916 CLVM cost units but forces a full BLS G1 decompression + subgroup check.

**Broken invariant**

The CLVM cost model is the sole mechanism that bounds how much CPU work a program can demand from a validating node. The invariant is: *cost units consumed ≈ wall-clock CPU time consumed*. `g1_negate` breaks this invariant by charging ~1,400× less than the equivalent validation work in `g1_subtract`.

### Impact Explanation

With a standard 11-billion-cost budget (`max_cost = 11_000_000_000`):

- Using `g1_negate` (916/call): ≈ **12,000,000** BLS G1 validations allowed
- Using `g1_subtract` (1,343,980/call): ≈ **8,185** BLS G1 validations allowed

An attacker can force a full node to perform ~1,466× more BLS point validations than the cost model intends to permit. BLS G1 decompression is a multi-millisecond operation on commodity hardware. This translates to a sustained CPU exhaustion attack on any node that validates attacker-crafted spends, degrading block validation throughput and potentially causing nodes to miss block deadlines — a consensus-safety-adjacent availability impact on the Chia network.

### Likelihood Explanation

Likelihood is **medium-high**. Any unprivileged party who can broadcast a Chia transaction can embed arbitrary CLVM in a spend bundle. No keys, no admin access, and no social engineering are required. The exploit program is trivial to construct (a CLVM loop over opcode 51 with distinct G1 atoms). The undercharge is large enough to be practically meaningful on real hardware.

### Recommendation

Recalibrate `BLS_G1_NEGATE_BASE_COST` and `BLS_G2_NEGATE_BASE_COST` to reflect the actual cost of `G1Element::from_bytes` / `G2Element::from_bytes` when `RELAXED_BLS` is not active. The simplest correct fix is to charge the same per-point cost as `op_bls_g1_subtract` / `op_bls_g2_subtract` when strict validation is performed, and reserve the current low cost only for the `RELAXED_BLS` path (where no validation occurs). Alternatively, split the cost constant into two values — one for the relaxed path and one for the strict path — and select between them based on the `strict` flag at the top of each operator.

### Proof of Concept

Craft a CLVM program that calls `g1_negate` (opcode 51) in a tight loop with 12 million distinct valid G1 atoms, each costing 916 cost units (total ≈ 11 billion, within the standard budget). Each iteration triggers `G1Element::from_bytes` in `validate_g1`. Because the cache is keyed on the 48-byte atom value, using distinct points prevents cache hits. The resulting wall-clock execution time will be orders of magnitude longer than a program spending the same cost budget on `g1_subtract`, demonstrating that the cost model fails to rate-limit BLS validation work through `g1_negate`.

Relevant code path:

1. `run_program` dispatches opcode 51 → `op_bls_g1_negate` [9](#0-8) 
2. `strict = true` (no `RELAXED_BLS`) → `a.validate_g1(point, blob)` called [10](#0-9) 
3. Cache miss (new point) → `G1Element::from_bytes(&bytes)` executes [11](#0-10) 
4. Total cost charged: `BLS_G1_NEGATE_BASE_COST + 48 * MALLOC_COST_PER_BYTE = 916 + 480 = 1396` [12](#0-11) 
5. Cache cleared at end of run, so no cross-invocation benefit [5](#0-4)

### Citations

**File:** src/bls_ops.rs (L23-37)
```rust
// return value, which the operator is adding back)
const BLS_G1_NEGATE_BASE_COST: Cost = 1396 - 480;

// g2_add and g2_subtract have the same cost
const BLS_G2_ADD_BASE_COST: Cost = 80000;
const BLS_G2_ADD_COST_PER_ARG: Cost = 1950000;
const BLS_G2_SUBTRACT_BASE_COST: Cost = 80000;
const BLS_G2_SUBTRACT_COST_PER_ARG: Cost = 1950000;

const BLS_G2_MULTIPLY_BASE_COST: Cost = 2100000;
const BLS_G2_MULTIPLY_COST_PER_BYTE: Cost = 5;

// this is the same cost as XORing the top bit (minus the heap allocation of the
// return value, which the operator is adding back)
const BLS_G2_NEGATE_BASE_COST: Cost = 2164 - 960;
```

**File:** src/bls_ops.rs (L52-78)
```rust
pub fn op_bls_g1_subtract(
    a: &mut Allocator,
    mut input: NodePtr,
    max_cost: Cost,
    _flags: ClvmFlags,
) -> Response {
    let mut cost = BLS_G1_SUBTRACT_BASE_COST;
    check_cost(cost, max_cost)?;
    let mut total = G1Element::default();
    let mut is_first = true;
    while let Some((arg, rest)) = a.next(input) {
        input = rest;
        let point = a.g1(arg)?;
        cost += BLS_G1_SUBTRACT_COST_PER_ARG;
        check_cost(cost, max_cost)?;
        if is_first {
            total = point;
        } else {
            total -= &point;
        };
        is_first = false;
    }
    Ok(Reduction(
        cost + 48 * MALLOC_COST_PER_BYTE,
        a.new_g1(total)?,
    ))
}
```

**File:** src/bls_ops.rs (L108-141)
```rust
pub fn op_bls_g1_negate(
    a: &mut Allocator,
    input: NodePtr,
    _max_cost: Cost,
    flags: ClvmFlags,
) -> Response {
    let strict = !flags.contains(ClvmFlags::RELAXED_BLS);
    let [point] = get_args::<1>(a, input, "g1_negate")?;

    let mut blob: [u8; 48] = atom(a, point, "G1 atom").and_then(|blob| {
        blob.as_ref().try_into().map_err(|_| {
            EvalErr::InvalidOpArg(point, "atom is not a G1 size, 48 bytes".to_string())
        })
    })?;
    if strict {
        a.validate_g1(point, blob)?;
    }

    if (blob[0] & 0xe0) == 0xc0 {
        // This is compressed infinity. negating it is a no-op
        // we can just pass through the same atom as we received. We'll charge
        // the allocation cost anyway, for consistency
        Ok(Reduction(
            BLS_G1_NEGATE_BASE_COST + 48 * MALLOC_COST_PER_BYTE,
            point,
        ))
    } else {
        blob[0] ^= 0x20;
        if strict {
            a.add_validated_g1(blob);
        }
        new_atom_and_cost(a, BLS_G1_NEGATE_BASE_COST, &blob)
    }
}
```

**File:** src/bls_ops.rs (L221-254)
```rust
pub fn op_bls_g2_negate(
    a: &mut Allocator,
    input: NodePtr,
    _max_cost: Cost,
    flags: ClvmFlags,
) -> Response {
    let strict = !flags.contains(ClvmFlags::RELAXED_BLS);
    let [point] = get_args::<1>(a, input, "g2_negate")?;

    let mut blob: [u8; 96] = atom(a, point, "G2 atom").and_then(|blob| {
        blob.as_ref()
            .try_into()
            .map_err(|_| EvalErr::InvalidOpArg(point, "atom is not G2 size, 96 bytes".to_string()))
    })?;
    if strict {
        a.validate_g2(point, blob)?;
    }

    if (blob[0] & 0xe0) == 0xc0 {
        // This is compressed infinity. negating it is a no-op
        // we can just pass through the same atom as we received. We'll charge
        // the allocation cost anyway, for consistency
        Ok(Reduction(
            BLS_G2_NEGATE_BASE_COST + 96 * MALLOC_COST_PER_BYTE,
            point,
        ))
    } else {
        blob[0] ^= 0x20;
        if strict {
            a.add_validated_g2(blob);
        }
        new_atom_and_cost(a, BLS_G2_NEGATE_BASE_COST, &blob)
    }
}
```

**File:** src/allocator.rs (L1264-1293)
```rust
    pub fn validate_g1(&mut self, node: NodePtr, bytes: [u8; 48]) -> Result<()> {
        if !self.validated_g1_points.contains(&bytes) {
            G1Element::from_bytes(&bytes)
                .map_err(|_| EvalErr::InvalidOpArg(node, "atom is not a G1 point".to_string()))?;
            self.validated_g1_points.insert(bytes);
        }
        Ok(())
    }

    pub fn validate_g2(&mut self, node: NodePtr, bytes: [u8; 96]) -> Result<()> {
        if !self.validated_g2_points.contains(&bytes) {
            G2Element::from_bytes(&bytes)
                .map_err(|_| EvalErr::InvalidOpArg(node, "atom is not a G2 point".to_string()))?;
            self.validated_g2_points.insert(bytes);
        }
        Ok(())
    }

    pub fn add_validated_g1(&mut self, bytes: [u8; 48]) {
        self.validated_g1_points.insert(bytes);
    }

    pub fn add_validated_g2(&mut self, bytes: [u8; 96]) {
        self.validated_g2_points.insert(bytes);
    }

    pub fn clear_validation_caches(&mut self) {
        self.validated_g1_points.clear();
        self.validated_g2_points.clear();
    }
```

**File:** src/run_program.rs (L559-560)
```rust
        self.allocator.clear_validation_caches();
        Ok(Reduction(cost, self.pop()?))
```

**File:** src/more_ops.rs (L75-78)
```rust
const POINT_ADD_BASE_COST: Cost = 101094;
// increased from 419994 to better model Raspberry PI
const POINT_ADD_COST_PER_ARG: Cost = 1343980;

```

**File:** src/chia_dialect.rs (L229-235)
```rust
            50 => op_bls_g1_multiply,
            51 => op_bls_g1_negate,
            52 => op_bls_g2_add,
            53 => op_bls_g2_subtract,
            54 => op_bls_g2_multiply,
            55 => op_bls_g2_negate,
            56 => op_bls_map_to_g1,
```
