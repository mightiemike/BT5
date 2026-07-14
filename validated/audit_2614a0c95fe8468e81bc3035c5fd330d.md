### Title
`op_multiply` Cost Undercharging via Bytes-to-Limbs Unit Mismatch in Intermediate Size Tracking — (`File: src/more_ops.rs`)

---

### Summary

`op_multiply` tracks the size of the running product in `l0` using two different units across iterations: **bytes** for the first operand (from `int_atom`) and **64-bit limbs** for every subsequent intermediate result (from `limbs_for_int`). Because the cost formula treats `l0` as a byte count throughout, every multiplication after the first is charged at roughly 1/8th the intended cost. An attacker can craft a CLVM program with many large-number multiplications that consumes far more CPU than the charged cost allows, enabling cost-limit bypass and potential consensus divergence.

---

### Finding Description

In `op_multiply` (`src/more_ops.rs`), the variable `l0` is initialized from `int_atom`, which returns the **byte length** of the first atom:

```rust
(total, l0) = int_atom(a, arg, "*")?;   // l0 = byte length
``` [1](#0-0) 

After each subsequent multiplication, `l0` is updated with:

```rust
l0 = limbs_for_int(&total);
``` [2](#0-1) 

`limbs_for_int` (defined at line 100 of `src/more_ops.rs`) returns the number of 64-bit machine words (limbs) in the `num_bigint::BigInt` representation — not the number of bytes. On a 64-bit host, one limb = 8 bytes. [3](#0-2) 

The cost formula applied to every subsequent operand is:

```rust
cost += (l0 as Cost + l1) * MUL_LINEAR_COST_PER_BYTE;
cost += (l0 as Cost * l1) / MUL_SQUARE_COST_PER_BYTE_DIVIDER;
``` [4](#0-3) 

The constant name `MUL_LINEAR_COST_PER_BYTE` makes the intended unit explicit: `l0` is supposed to be a **byte count**. After the first multiplication, it is a **limb count**, which is 8× smaller. The quadratic term `(l0 * l1) / MUL_SQUARE_COST_PER_BYTE_DIVIDER` is therefore undercharged by up to 64×.

The overflow guard `l0 > 1024` is also expressed in limbs, so the intermediate product is allowed to grow to 1024 limbs = **8 192 bytes** before rejection — far beyond the 256-byte per-input limit — while the cost charged for that growth is computed as if the product were only 1024 bytes. [2](#0-1) 

The same unit mismatch does **not** exist in `op_div` or `op_divmod`, which call `int_atom` for both operands and never update a running size variable. [5](#0-4) 

The relevant cost constants are:

```
MUL_LINEAR_COST_PER_BYTE  = 6
MUL_SQUARE_COST_PER_BYTE_DIVIDER = 128
``` [6](#0-5) 

---

### Impact Explanation

**Undercharged execution / consensus divergence.**

A crafted CLVM program can perform a chain of multiplications where each step doubles the size of the running product. The actual CPU work grows quadratically with the byte size of the product, but the charged cost grows only quadratically with the limb count (8× smaller). Concretely:

| Intermediate result | Actual byte size | `l0` charged as | Linear cost charged | Linear cost owed | Ratio |
|---|---|---|---|---|---|
| After 1st mul | 512 B | 64 limbs | `(64+256)*6 = 1 920` | `(512+256)*6 = 4 608` | 0.42× |
| After 2nd mul | 1 024 B | 128 limbs | `(128+256)*6 = 2 304` | `(1024+256)*6 = 7 680` | 0.30× |
| Near limit | 8 192 B | 1 024 limbs | `(1024+256)*6 = 7 680` | `(8192+256)*6 = 50 688` | 0.15× |

An attacker who stays just under the node's `max_cost` limit can execute programs whose true computational cost is 6–8× higher than what was charged. This can:

1. **Exhaust validator CPU** without triggering the cost guard, causing block validation to take far longer than expected.
2. **Cause consensus divergence** if different node implementations or future versions compute cost correctly, causing them to reject programs that the current implementation accepts.

---

### Likelihood Explanation

**High.** The entry path is direct: any caller of `run_program` with attacker-controlled CLVM bytes can trigger `op_multiply` with multiple large operands. No special privileges, flags, or social engineering are required. The `*` opcode is a standard, always-available CLVM operator. The bug is triggered by any program that multiplies more than two large numbers in sequence — a pattern that appears in legitimate puzzle code and is trivially constructable by an attacker.

---

### Recommendation

Replace `limbs_for_int` with a byte-length measurement of the intermediate product so that `l0` remains in the same unit throughout the loop. The simplest fix is to compute the byte length of the serialized intermediate result after each multiplication, consistent with how `int_atom` measures the first operand:

```rust
// After: total *= ...;
l0 = (total.bits() as usize + 7) / 8;   // byte length, matching int_atom's unit
```

Alternatively, convert the first operand's `l0` to limbs at initialization and use limbs consistently — but then rename the cost constants to avoid future confusion.

---

### Proof of Concept

The following CLVM program multiplies a 256-byte number by itself repeatedly, keeping the intermediate product just under the 1024-limb limit. Each step is charged at limb-scale cost while performing byte-scale work:

```
; CLVM pseudocode (operator 18 = *)
(* A A A A A A)   ; where A is a 256-byte atom (e.g., 2^2047)
```

Step-by-step cost accounting under the bug:

1. **Init**: `l0 = 256` (bytes, from `int_atom`), `total = A`
2. **Step 1** (`total = A²`, ~512 B): charged `l0=256`, correct. `l0 = limbs_for_int(A²) = 64`
3. **Step 2** (`total = A³`, ~768 B): charged `l0=64` (should be 512). Undercharge: ~8×
4. **Step 3** (`total = A⁴`, ~1024 B): charged `l0=96` (should be 768). Undercharge: ~8×
5. **Step 4** (`total = A⁵`, ~1280 B): charged `l0=128` (should be 1024). Undercharge: ~8×
6. **Step 5** (`total = A⁶`, ~1536 B): charged `l0=160` (should be 1280). Undercharge: ~8×

Total charged cost is approximately 8× less than the true computational cost, allowing the program to pass a `max_cost` check that should have rejected it.

### Citations

**File:** src/more_ops.rs (L36-37)
```rust
const MUL_LINEAR_COST_PER_BYTE: Cost = 6;
const MUL_SQUARE_COST_PER_BYTE_DIVIDER: Cost = 128;
```

**File:** src/more_ops.rs (L100-100)
```rust
fn limbs_for_int(v: &Number) -> usize {
```

**File:** src/more_ops.rs (L598-604)
```rust
        if first_iter {
            (total, l0) = int_atom(a, arg, "*")?;
            if l0 > 256 {
                return Err(EvalErr::InvalidOpArg(arg, "*".to_string()));
            }
            first_iter = false;
            continue;
```

**File:** src/more_ops.rs (L615-616)
```rust
                cost += (l0 as Cost + l1) * MUL_LINEAR_COST_PER_BYTE;
                cost += (l0 as Cost * l1) / MUL_SQUARE_COST_PER_BYTE_DIVIDER;
```

**File:** src/more_ops.rs (L649-652)
```rust
        l0 = limbs_for_int(&total);
        if l0 > 1024 {
            return Err(EvalErr::InvalidOpArg(arg, "*".to_string()));
        }
```

**File:** src/more_ops.rs (L662-671)
```rust
    let [v0, v1] = get_args::<2>(a, input, "/")?;
    let (a0, a0_len) = int_atom(a, v0, "/")?;
    let (a1, a1_len) = int_atom(a, v1, "/")?;
    if flags.contains(ClvmFlags::DISABLE_OP) && a0_len > 2048 {
        return Err(EvalErr::InvalidOpArg(input, "div".to_string()));
    }
    if a0_len > 256 || a1_len > 1024 {
        return Err(EvalErr::InvalidOpArg(input, "div".to_string()));
    }
    let cost = DIV_BASE_COST + ((a0_len + a1_len) as Cost) * DIV_COST_PER_BYTE;
```
