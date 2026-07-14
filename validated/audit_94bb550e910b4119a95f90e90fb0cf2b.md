### Title
`op_multiply` Intermediate-Product Size Systematically Under-Reported by `limbs_for_int`, Enabling Undercharged Execution — (File: `src/more_ops.rs`)

---

### Summary

In `op_multiply`, the running accumulator byte-length `l0` is seeded from `int_atom` (which returns the true signed CLVM byte length) but is updated after every multiplication via `limbs_for_int(&total)`, which computes only the **magnitude** byte count (`ceil(bits/8)`). For any intermediate product whose most-significant magnitude bit is set (`bits % 8 == 0`), `limbs_for_int` returns a value exactly **1 byte shorter** than the actual signed representation stored by `new_number`. This is the direct analog of H-03: a partial/incomplete measure of size is substituted for the full measure, causing every subsequent cost step to be under-charged.

---

### Finding Description

`limbs_for_int` is defined as:

```rust
fn limbs_for_int(v: &Number) -> usize {
    v.bits().div_ceil(8) as usize
}
```

`Number::bits()` returns the number of bits in the **absolute value**, not counting the sign bit. For a positive integer whose magnitude exactly fills a whole byte boundary (e.g., 128 = `0x80`, 255 = `0xFF`, 32768 = `0x8000`, 16777215 = `0xFFFFFF`), `bits() % 8 == 0`, so `div_ceil(bits, 8) == bits / 8`. But CLVM stores integers as minimal signed big-endian: when the MSB of the magnitude is set, `new_number` prepends a `0x00` sign-extension byte, making the stored atom **1 byte longer**.

The test at lines 104–116 explicitly encodes this: for `[0x00, 0xff]` (the number 255), `limb_test_helper` expects `bytes.len() - 1 = 1` (stripping the leading zero), and `limbs_for_int` returns 1. But `int_atom` — which calls `a.atom_len(args)` — returns 2 for the same number, because that is the actual stored byte length.

In `op_multiply`:

- **First argument** (line 599): `(total, l0) = int_atom(a, arg, "*")?` — `l0` is the true stored byte length.
- **Every subsequent step** (line 649): `l0 = limbs_for_int(&total)` — `l0` is the magnitude byte length, potentially 1 byte short.

The cost formula applied at lines 615–616 and 623–624 is:

```rust
cost += (l0 as Cost + l1) * MUL_LINEAR_COST_PER_BYTE;       // 6 per byte
cost += (l0 as Cost * l1) / MUL_SQUARE_COST_PER_BYTE_DIVIDER; // quadratic / 128
```

When `l0` is under-reported by 1, the under-charge per step is `6 + l1/128` cost units. For the maximum operand size `l1 = 256`, this is `6 + 2 = 8` cost units per step.

Additionally, the size guard at line 650 (`if l0 > 1024`) uses the under-reported `l0`. An intermediate product of actual stored size 1025 bytes passes the guard when `l0 = 1024`, silently exceeding the intended 1024-byte limit.

---

### Impact Explanation

The cost model is the primary DoS defence for CLVM execution. Under-charging `op_multiply` allows an attacker to submit programs whose true computational cost exceeds the declared cost limit by a bounded but non-zero margin. Because all nodes run the same `limbs_for_int` code, they all compute the same under-charged cost — so this is not a consensus divergence. The impact is that programs slightly exceeding the intended cost budget are accepted by every node, allowing slightly more computation per block than the protocol intends. The maximum cumulative under-charge over a chain of multiplications is bounded by the number of steps times ~8 cost units per step, which is a small but real fraction of the block cost limit.

---

### Likelihood Explanation

Triggering the bug requires only that intermediate products have their MSB set, which is trivially achievable: supply a first argument of the form `0x80 || [0x00 * N]` (N ≤ 255 bytes) and multiply by similar values. No special privileges, no compromised nodes, and no social engineering are required. Any caller of `run_program` with attacker-controlled CLVM bytes — including the Chia mempool and block-validation path — is a reachable entry point.

---

### Recommendation

Replace `limbs_for_int(&total)` at line 649 with a function that returns the actual signed CLVM byte length of the intermediate product. The correct formula for a positive `Number` is `(bits + 8) / 8` when `bits % 8 == 0`, and `bits.div_ceil(8)` otherwise — equivalently, `(bits + 1).div_ceil(8)` for non-negative values. For negative values the same off-by-one applies when `bits % 8 == 0`. Alternatively, allocate the intermediate result into the `Allocator` and use `a.atom_len(node)` as the authoritative byte count, consistent with how `int_atom` seeds the initial `l0`.

---

### Proof of Concept

Consider the program `(* A B B B ...)` where:

- `A` = `[0x00, 0x80, 0x00, ..., 0x00]` — 256 bytes, value = 2^(8×255). `int_atom` returns `l0 = 256`.
- `B` = `[0x00, 0x80]` — 2 bytes, value = 128.

After the first multiplication `A * B`:
- Result = 2^(8×255) × 128 = 2^(8×255+7). `bits() = 8×255+7 = 2047`. `limbs_for_int = ceil(2047/8) = 256`. Correct here (MSB not at a byte boundary).

Now choose `A = [0x80, 0x00, ..., 0x00]` (255 bytes, value = 2^(8×254)). `int_atom` returns `l0 = 255`.

After `A * B` (B = `[0x80]`, value = 128):
- Result = 2^(8×254) × 128 = 2^(8×254+7). `bits() = 8×255 = 2040`. `limbs_for_int = 2040/8 = 255`. But `new_number` stores this as `[0x00, 0x80, 0x00, ..., 0x00]` = **256 bytes** (sign-extension byte prepended). `l0` is set to 255 instead of 256.

The next multiplication step charges `(255 + l1) * 6 + (255 * l1) / 128` instead of `(256 + l1) * 6 + (256 * l1) / 128`, under-charging by `6 + l1/128` cost units. Repeating this pattern across many steps accumulates the under-charge.

**Exact lines**: [1](#0-0) [2](#0-1) [3](#0-2) [4](#0-3) [5](#0-4)

### Citations

**File:** src/more_ops.rs (L100-102)
```rust
fn limbs_for_int(v: &Number) -> usize {
    v.bits().div_ceil(8) as usize
}
```

**File:** src/more_ops.rs (L598-605)
```rust
        if first_iter {
            (total, l0) = int_atom(a, arg, "*")?;
            if l0 > 256 {
                return Err(EvalErr::InvalidOpArg(arg, "*".to_string()));
            }
            first_iter = false;
            continue;
        }
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

**File:** src/op_utils.rs (L248-256)
```rust
pub fn int_atom(a: &Allocator, args: NodePtr, op_name: &str) -> Result<(Number, usize)> {
    match a.sexp(args) {
        SExp::Atom => Ok((a.number(args), a.atom_len(args))),
        _ => Err(EvalErr::InvalidOpArg(
            args,
            format!("Requires Int Argument: {op_name}"),
        ))?,
    }
}
```
