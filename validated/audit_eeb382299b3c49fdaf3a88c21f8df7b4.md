### Title
`op_modpow` Cost Formula Missing Cross-Term Between Exponent and Modulus Size Leads to Systematic Undercharging - (File: src/more_ops.rs)

### Summary

The cost formula in `op_modpow` (and its Malachite variant) uses two independent quadratic terms — one for exponent byte-size and one for modulus byte-size — but the actual computational complexity of modular exponentiation is `O(esize × msize²)`. The formula is missing the cross-product term, causing systematic undercharging when both operands are large. An attacker can craft a CLVM program that is accepted at a low declared cost but consumes far more CPU than the cost limit is designed to bound.

### Finding Description

In `src/more_ops.rs`, `op_modpow` computes its cost as:

```rust
cost += bsize as Cost * MODPOW_COST_PER_BYTE_BASE_VALUE;   // linear in base
cost += (esize * esize) as Cost * MODPOW_COST_PER_BYTE_EXPONENT; // esize²
cost += (msize * msize) as Cost * MODPOW_COST_PER_BYTE_MOD;     // msize²
``` [1](#0-0) 

The constants are:

```rust
const MODPOW_COST_PER_BYTE_EXPONENT: Cost = 3;
const MODPOW_COST_PER_BYTE_MOD: Cost = 21;
``` [2](#0-1) 

The same formula is replicated verbatim in `op_modpow_malachite`: [3](#0-2) 

And the test-vector generator encodes the same formula, so tests cannot detect the mismatch: [4](#0-3) 

**Why the formula is wrong:**

Modular exponentiation (binary square-and-multiply) performs `O(esize × 8)` modular multiplications. Each multiplication of two `msize`-byte numbers costs `O(msize²)` (schoolbook) or `O(msize^1.585)` (Karatsuba). The total real cost is therefore `O(esize × msize²)`, a cross-product term that is entirely absent from the formula.

The formula instead charges `esize² × 3 + msize² × 21`, which is the sum of two independent quadratic terms. These two expressions diverge dramatically at the allowed maximum of 256 bytes for each operand:

| Metric | Value |
|---|---|
| Formula cost (esize=256, msize=256) | `256²×3 + 256²×21 ≈ 1,572,864` |
| Actual multiplications | `256×8 = 2,048` |
| Cost per multiplication (msize=256) | `O(256²) = O(65,536)` |
| Actual work | `~2,048 × 65,536 ≈ 134,217,728` |
| Undercharge ratio | **~85×** |

### Impact Explanation

The CLVM cost system is the primary DoS defence for the Chia consensus layer. Every full node enforces a `max_cost` budget per block/transaction. If `op_modpow` is undercharged by ~85× at maximum operand sizes, an attacker can include a program whose declared cost fits within the block budget but whose actual CPU consumption is ~85× higher. This is a consensus-critical undercharged-execution vulnerability: every validating node must execute the program and will be stalled proportionally longer than the cost model predicts.

### Likelihood Explanation

`op_modpow` is a first-class CLVM operator reachable from any attacker-supplied program bytes. The attacker only needs to supply a 256-byte exponent and a 256-byte modulus — both within the enforced size limit (`bsize > 256 || esize > 256 || msize > 256` check at line 1266). No special permissions, keys, or social engineering are required. The attack is repeatable across every block. [5](#0-4) 

### Recommendation

Replace the two independent quadratic terms with the correct cross-product formula that reflects actual modpow complexity:

```rust
// Correct: cost scales as esize × msize²
cost += (esize as Cost) * (msize as Cost) * (msize as Cost) * MODPOW_COST_PER_BYTE_CROSS;
```

The constant `MODPOW_COST_PER_BYTE_CROSS` should be calibrated against benchmarks on the slowest supported hardware (e.g., Raspberry Pi 4, as already done for BLS operations). The base-value linear term and the result-allocation term can remain unchanged.

### Proof of Concept

Craft a CLVM program:

```
(modpow <256-byte base> <256-byte exponent> <256-byte odd modulus>)
```

With the current formula, the declared cost is approximately:

```
17000 + 256×38 + 256²×3 + 256²×21 + result_len×10
≈ 1,602,152
```

A node with `max_cost = 11,000,000,000` (Chia's block limit) can therefore accept roughly **6,800** such calls in a single block. Each call performs ~134 million schoolbook-multiplication-equivalent operations. The total real work is `6,800 × 134M ≈ 910 billion` operations — far exceeding what the cost model permits — causing every validating node to spend orders of magnitude more CPU than the budget implies, constituting a practical denial-of-service against block validation.

### Citations

**File:** src/more_ops.rs (L97-98)
```rust
const MODPOW_COST_PER_BYTE_EXPONENT: Cost = 3;
const MODPOW_COST_PER_BYTE_MOD: Cost = 21;
```

**File:** src/more_ops.rs (L1256-1263)
```rust
    let mut cost = MODPOW_BASE_COST;
    let (base, bsize) = int_atom(a, base, "modpow")?;
    cost += bsize as Cost * MODPOW_COST_PER_BYTE_BASE_VALUE;
    let (exponent, esize) = int_atom(a, exponent, "modpow")?;
    cost += (esize * esize) as Cost * MODPOW_COST_PER_BYTE_EXPONENT;
    check_cost(cost, max_cost)?;
    let (modulus, msize) = int_atom(a, modulus, "modpow")?;
    cost += (msize * msize) as Cost * MODPOW_COST_PER_BYTE_MOD;
```

**File:** src/more_ops.rs (L1266-1268)
```rust
    if bsize > 256 || esize > 256 || msize > 256 {
        return Err(EvalErr::InvalidOpArg(input, "modpow".to_string()));
    }
```

**File:** src/more_ops.rs (L1294-1302)
```rust
    let mut cost = MODPOW_BASE_COST;
    let (base, bsize) = malachite_int_atom(a, base, "modpow")?;
    cost += bsize as Cost * MODPOW_COST_PER_BYTE_BASE_VALUE;
    let (exponent, esize) = malachite_int_atom(a, exponent, "modpow")?;
    cost += (esize * esize) as Cost * MODPOW_COST_PER_BYTE_EXPONENT;
    check_cost(cost, max_cost)?;
    let (modulus, msize) = malachite_int_atom(a, modulus, "modpow")?;
    cost += (msize * msize) as Cost * MODPOW_COST_PER_BYTE_MOD;
    check_cost(cost, max_cost)?;
```

**File:** tools/src/bin/generate-modpow-tests.rs (L42-46)
```rust
        let cost = 17000
            + base_len * 38
            + exponent_len * exponent_len * 3
            + modulus_len * modulus_len * 21
            + result_len * 10;
```
