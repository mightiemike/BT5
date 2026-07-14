### Title
Severely Undercharged Execution Cost for `op_bls_g1_negate` and `op_bls_g2_negate` Uses Proxy XOR Cost Instead of Actual BLS Validation Cost — (`File: src/bls_ops.rs`)

---

### Summary

`BLS_G1_NEGATE_BASE_COST` and `BLS_G2_NEGATE_BASE_COST` in `src/bls_ops.rs` are set to the cost of a simple bit-flip (XOR top bit), not the actual cost of the operations, which include expensive BLS12-381 point deserialization and validation. The repository's own `benchmarks.txt` shows the true cost is ~850,000–856,000 units for `g1_negate` and ~2,376,000–2,440,000 units for `g2_negate`, yet the charged costs are 1,396 and 2,164 respectively — an undercharge of roughly **600–1,100×**. An attacker can craft CLVM programs that call these opcodes repeatedly, consuming far more CPU than the cost budget implies, enabling resource exhaustion and potential consensus divergence.

---

### Finding Description

In `src/bls_ops.rs`, the cost constants for the two negate operators are defined as:

```rust
// this is the same cost as XORing the top bit (minus the heap allocation of the
// return value, which the operator is adding back)
const BLS_G1_NEGATE_BASE_COST: Cost = 1396 - 480;   // = 916

// this is the same cost as XORing the top bit (minus the heap allocation of the
// return value, which the operator is adding back)
const BLS_G2_NEGATE_BASE_COST: Cost = 2164 - 960;   // = 1204
```

The comment explicitly states the rationale: these costs are borrowed from the cost of a simple XOR-bit-flip operation, not from a measurement of what `g1_negate` and `g2_negate` actually do.

The actual implementations, however, do far more than flip a bit. In strict mode (the default, when `RELAXED_BLS` is not set), both operators call `a.validate_g1(point, blob)` / `a.validate_g2(point, blob)` — full BLS12-381 point deserialization and subgroup-membership validation — before performing the XOR:

```rust
pub fn op_bls_g1_negate(
    a: &mut Allocator,
    input: NodePtr,
    _max_cost: Cost,          // ← max_cost is IGNORED (underscore prefix)
    flags: ClvmFlags,
) -> Response {
    let strict = !flags.contains(ClvmFlags::RELAXED_BLS);
    let [point] = get_args::<1>(a, input, "g1_negate")?;
    let mut blob: [u8; 48] = atom(a, point, "G1 atom").and_then(|blob| { ... })?;
    if strict {
        a.validate_g1(point, blob)?;   // ← expensive BLS validation
    }
    ...
    blob[0] ^= 0x20;                   // ← the actual "negate" (one bit flip)
    new_atom_and_cost(a, BLS_G1_NEGATE_BASE_COST, &blob)  // charges only 916
}
```

The total cost charged per call is:
- `g1_negate`: `916 + 48 × 10 = 1,396`
- `g2_negate`: `1,204 + 96 × 10 = 2,164`

The repository's own `benchmarks.txt` records the true measured cost (derived from wall-clock timing × cost scale of 6.425):

| Opcode | Benchmark cost (run 1) | Benchmark cost (run 2) | Benchmark cost (run 3) | Charged cost | Undercharge factor |
|---|---|---|---|---|---|
| `g1_negate` (51) | 850,233 | 839,869 | 856,323 | **1,396** | **~609×** |
| `g2_negate` (55) | 2,376,799 | 2,390,494 | 2,440,188 | **2,164** | **~1,098×** |

The `_max_cost` parameter is intentionally unused (underscore prefix) in both functions, so there is no early-exit guard — the full BLS validation always runs to completion regardless of the remaining cost budget.

This is structurally identical to the WBTC/BTC oracle bug: the cost of the *underlying primitive* (XOR one bit) is used as a proxy for the cost of the *derived operation* (BLS point validation + XOR), without accounting for the expensive work the derived operation actually performs.

---

### Impact Explanation

An attacker submits a CLVM program that calls `g1_negate` (opcode 51) or `g2_negate` (opcode 55) in a tight loop with fresh, attacker-controlled G1/G2 atoms. Each call:

1. Charges only 1,396 (or 2,164) cost units.
2. Actually performs ~130,000–380,000 ns of BLS point validation.

With Chia's block cost limit of 11,000,000,000 units, an attacker can fit approximately:
- `11,000,000,000 / 1,396 ≈ 7,879,656` calls to `g1_negate` within budget.
- At the true cost of ~850,000 per call, those calls would require `7,879,656 × 850,000 ≈ 6.7 × 10¹²` cost units of real computation — roughly **609× the intended budget**.

Concrete impacts:
- **Resource exhaustion / DoS**: Nodes spend orders of magnitude more CPU than the cost budget implies, stalling block validation.
- **Consensus divergence**: Slower nodes (e.g., Raspberry Pi, the explicit design target per `more_ops.rs` comments) may time out or reject blocks that faster nodes accept, splitting consensus.
- **Mempool abuse**: Transactions that appear cheap by cost accounting are actually expensive to validate, degrading mempool throughput.

---

### Likelihood Explanation

- Opcodes 51 (`g1_negate`) and 55 (`g2_negate`) are in the main operator dispatch table in `src/chia_dialect.rs` and are reachable from any CLVM program submitted to the network.
- No special flags or permissions are required; the default `ChiaDialect` exposes both opcodes unconditionally.
- The attacker only needs to craft a valid CLVM program that calls these opcodes in a loop with valid 48-byte / 96-byte atoms.
- The `RELAXED_BLS` flag (which skips validation) is a hard-fork flag not set in normal operation, so strict validation runs by default.

---

### Recommendation

Replace the proxy XOR-bit-flip cost with a cost derived from actual benchmarks, consistent with how all other BLS operators are costed:

```rust
// Measured: ~850,000 cost units (benchmarks.txt, three runs)
const BLS_G1_NEGATE_BASE_COST: Cost = 850_000;

// Measured: ~2,400,000 cost units (benchmarks.txt, three runs)
const BLS_G2_NEGATE_BASE_COST: Cost = 2_400_000;
```

Additionally, restore the `max_cost` early-exit check (remove the underscore prefix and call `check_cost`) so that programs already over budget cannot trigger the expensive validation:

```rust
pub fn op_bls_g1_negate(
    a: &mut Allocator,
    input: NodePtr,
    max_cost: Cost,   // ← remove underscore
    flags: ClvmFlags,
) -> Response {
    check_cost(BLS_G1_NEGATE_BASE_COST, max_cost)?;  // ← add early guard
    ...
}
```

---

### Proof of Concept

**Attacker-controlled CLVM program** (pseudocode; any valid 48-byte G1 point `P`):

```
(a (q . (loop_body)) (q . ()))

; loop_body calls g1_negate 10,000 times on a fresh G1 atom
; Total charged cost: 10,000 × 1,396 = 13,960,000
; Actual CPU cost:    10,000 × ~130,000 ns = ~1.3 seconds of BLS validation
; At block limit (11B cost): ~7.8M calls → ~1,014 seconds of real CPU
```

**Concrete cost comparison from `benchmarks.txt`**:

- `g1_negate` measured time: `132,316 ns` × cost scale `6.425` = **850,233** cost units
- `g1_negate` charged cost: **1,396** cost units
- Ratio: **609×** undercharge

**Relevant code locations**: [1](#0-0) [2](#0-1) [3](#0-2) [4](#0-3) [5](#0-4) [6](#0-5) [7](#0-6)

### Citations

**File:** src/bls_ops.rs (L22-24)
```rust
// this is the same cost as XORing the top bit (minus the heap allocation of the
// return value, which the operator is adding back)
const BLS_G1_NEGATE_BASE_COST: Cost = 1396 - 480;
```

**File:** src/bls_ops.rs (L35-37)
```rust
// this is the same cost as XORing the top bit (minus the heap allocation of the
// return value, which the operator is adding back)
const BLS_G2_NEGATE_BASE_COST: Cost = 2164 - 960;
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

**File:** benchmarks.txt (L13-15)
```text
opcode: g1_negate (51)
   time: base: 132316.16ns
   cost: base: 850233
```

**File:** benchmarks.txt (L25-27)
```text
opcode: g2_negate (55)
   time: base: 369885.46ns
   cost: base: 2376799
```

**File:** src/chia_dialect.rs (L230-235)
```rust
            51 => op_bls_g1_negate,
            52 => op_bls_g2_add,
            53 => op_bls_g2_subtract,
            54 => op_bls_g2_multiply,
            55 => op_bls_g2_negate,
            56 => op_bls_map_to_g1,
```
