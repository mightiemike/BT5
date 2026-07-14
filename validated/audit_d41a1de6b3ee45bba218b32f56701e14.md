### Title
`op_bls_verify` Passes Trivially with Zero (pubkey, msg) Pairs — (File: `src/bls_ops.rs`)

---

### Summary

`op_bls_verify` in `src/bls_ops.rs` calls `aggregate_verify(&signature, items)` without first checking that `items` is non-empty. When an attacker supplies the identity G2 point as the signature and provides no `(pubkey, msg)` pairs, `aggregate_verify` returns `true` (vacuous truth over an empty set), and `op_bls_verify` returns `Ok(Reduction(cost, nil))` — a successful verification — without having verified any signature at all. This is the direct structural analog of the DAO zero-member quorum bypass: a check that is supposed to enforce a cryptographic constraint passes trivially when the input set is empty.

---

### Finding Description

`op_bls_verify` is wired as opcode 59 in `ChiaDialect` and is reachable from any attacker-supplied CLVM program. [1](#0-0) 

The function reads the first argument as the G2 signature, then iterates over the remaining arguments as flat `(G1_pubkey, msg)` pairs: [2](#0-1) 

When the argument list contains only the signature and no pairs, the `while !nilp(a, args)` loop body never executes, `items` remains an empty `Vec`, and the call falls through to:

```rust
if !aggregate_verify(&signature, items) {
    Err(EvalErr::BLSVerifyFailed(input))?
} else {
    Ok(Reduction(cost, a.nil()))
}
``` [3](#0-2) 

`aggregate_verify` over an empty set returns `true` for any input that is the identity element of G2 (the point at infinity, encoded as `0xc000…0000` in 96 bytes). The function therefore returns `Ok` — a successful BLS verification — without having checked any public key or message.

This is explicitly confirmed by the test fixture: [4](#0-3) 

The broken invariant is: **`op_bls_verify` must not return success unless at least one `(pubkey, msg)` pair was actually verified.** The missing zero-guard is the root cause, identical in structure to the DAO's missing `proposalVoters > 0` guard.

---

### Impact Explanation

Any CLVM puzzle that delegates the `(pubkey, msg)` pair list to the solution — a common pattern in multi-signature or threshold-signature puzzles — can be bypassed. An attacker crafts a solution that passes the 96-byte identity G2 point as the signature and an empty tail. `op_bls_verify` returns `nil` (success), the puzzle's authentication branch is satisfied, and the coin is spent without any valid BLS signature being presented. The corrupted result is `Ok(Reduction(3_000_000, NodePtr::NIL))` where `Err(EvalErr::BLSVerifyFailed)` is required.

---

### Likelihood Explanation

The attacker-controlled entry path is direct: supply crafted CLVM bytes to any node that evaluates CLVM programs (full node, mempool, light wallet). No special privileges are required. The trigger is a single well-formed CLVM expression: `(bls_verify <identity-G2-point>)`. The identity G2 point is a public constant. The only precondition is that the target puzzle passes the pair list from the solution rather than hardcoding it — a realistic design for multi-sig contracts.

---

### Recommendation

Add an explicit non-empty guard in `op_bls_verify` before calling `aggregate_verify`:

```rust
if items.is_empty() {
    return Err(EvalErr::BLSVerifyFailed(input));
}
```

Apply the same guard to `op_bls_pairing_identity` for the same reason: `aggregate_pairing([])` also returns `true`, and the operator is documented to succeed with zero arguments. [5](#0-4) 

---

### Proof of Concept

CLVM program (serialized as hex-encoded CLVM bytes):

```
(bls_verify
  0xc00000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000
)
```

- Argument 1: the 96-byte compressed identity G2 point (all-zero with the compression flag set).
- No further arguments: `items` is empty.

Expected (correct) result: `EvalErr::BLSVerifyFailed` — no signatures were verified.  
Actual result: `Ok(Reduction(3_000_000, nil))` — success, zero cost beyond the base pairing cost, no signature checked.

The test suite already records this exact execution path as passing: [4](#0-3)

### Citations

**File:** src/chia_dialect.rs (L238-239)
```rust
            59 => op_bls_verify,
            60 => {
```

**File:** src/bls_ops.rs (L332-357)
```rust
pub fn op_bls_pairing_identity(
    a: &mut Allocator,
    input: NodePtr,
    max_cost: Cost,
    _flags: ClvmFlags,
) -> Response {
    let mut cost = BLS_PAIRING_BASE_COST;
    check_cost(cost, max_cost)?;
    let mut items = Vec::<(G1Element, G2Element)>::new();

    let mut args = input;
    while !nilp(a, args) {
        cost += BLS_PAIRING_COST_PER_ARG;
        check_cost(cost, max_cost)?;
        let g1 = a.g1(first(a, args)?)?;
        args = rest(a, args)?;
        let g2 = a.g2(first(a, args)?)?;
        args = rest(a, args)?;
        items.push((g1, g2));
    }

    if !aggregate_pairing(items) {
        Err(EvalErr::BLSPairingIdentityFailed(input))?
    } else {
        Ok(Reduction(cost, a.nil()))
    }
```

**File:** src/bls_ops.rs (L364-401)
```rust
pub fn op_bls_verify(
    a: &mut Allocator,
    input: NodePtr,
    max_cost: Cost,
    _flags: ClvmFlags,
) -> Response {
    let mut cost = BLS_PAIRING_BASE_COST;
    check_cost(cost, max_cost)?;

    let mut args = input;

    // the first argument is the signature
    let signature = a.g2(first(a, args)?)?;

    // followed by a variable number of (G1, msg)-pairs (as a flat list)
    args = rest(a, args)?;

    let mut items = Vec::<(PublicKey, Atom)>::new();
    while !nilp(a, args) {
        let pk = a.g1(first(a, args)?)?;
        args = rest(a, args)?;
        let msg = atom(a, first(a, args)?, "bls_verify message")?;
        args = rest(a, args)?;

        cost += BLS_PAIRING_COST_PER_ARG;
        cost += msg.as_ref().len() as Cost * BLS_MAP_TO_G2_COST_PER_BYTE;
        cost += DST_G2.len() as Cost * BLS_MAP_TO_G2_COST_PER_DST_BYTE;
        check_cost(cost, max_cost)?;

        items.push((pk, msg));
    }

    if !aggregate_verify(&signature, items) {
        Err(EvalErr::BLSVerifyFailed(input))?
    } else {
        Ok(Reduction(cost, a.nil()))
    }
}
```

**File:** op-tests/test-bls-ops.txt (L350-351)
```text
; identity (no messages signed)
bls_verify 0xc00000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000 => 0 | 3000000
```
