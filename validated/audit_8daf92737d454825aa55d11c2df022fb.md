### Title
`op_bls_verify` and `op_bls_pairing_identity` Accept Zero-Pair Argument Lists, Trivially Bypassing Signature Verification - (File: `src/bls_ops.rs`)

---

### Summary

`op_bls_verify` and `op_bls_pairing_identity` impose no minimum count on the number of cryptographic pairs they aggregate. When called with zero `(pubkey, msg)` pairs (for `bls_verify`) or zero `(G1, G2)` pairs (for `bls_pairing_identity`), both operators succeed unconditionally. This is the direct analog of the reported missing minimum sample size: just as `medianPrice` with one source trivially returns that source's price, these operators with zero pairs trivially return success — the empty product is always the identity.

---

### Finding Description

**`op_bls_verify`** (`src/bls_ops.rs`, lines 364–401):

The function reads one mandatory G2 signature argument, then iterates over a variable-length flat list of `(G1_pubkey, msg)` pairs. There is no check that at least one pair is present:

```rust
// followed by a variable number of (G1, msg)-pairs (as a flat list)
args = rest(a, args)?;

let mut items = Vec::<(PublicKey, Atom)>::new();
while !nilp(a, args) {          // loop body never executes if args is nil
    ...
    items.push((pk, msg));
}

if !aggregate_verify(&signature, items) {   // items is empty
    Err(EvalErr::BLSVerifyFailed(input))?
} else {
    Ok(Reduction(cost, a.nil()))            // always reached with empty items
}
```

When `items` is empty, `aggregate_verify` is called with an empty set. By BLS specification, the aggregate of zero signatures is the G2 identity element (`0xc000…0000`). `aggregate_verify` returns `true` when the provided signature equals this identity, so any caller that passes the 96-byte infinity point as the signature and no `(pubkey, msg)` pairs receives a successful `Reduction`.

This is confirmed by the project's own test vector:

```
; identity (no messages signed)
bls_verify 0xc000...0000 => 0 | 3000000
```

**`op_bls_pairing_identity`** (`src/bls_ops.rs`, lines 332–358):

Identically, the loop over `(G1, G2)` pairs never executes when the argument list is nil. `aggregate_pairing([])` returns `true` (empty product = Gt identity), so the operator succeeds unconditionally:

```rust
while !nilp(a, args) { ... }   // never entered
if !aggregate_pairing(items) { // items == []
    Err(...)
} else {
    Ok(Reduction(cost, a.nil()))  // always reached
}
```

Test vector confirmation:

```
; identity
bls_pairing_identity => 0 | 3000000
```

---

### Impact Explanation

Any CLVM puzzle that uses `bls_verify` or `bls_pairing_identity` to gate coin spending, and whose argument list to those operators is constructed (even partially) from the attacker-controlled solution, can be bypassed. The attacker provides:

- For `bls_verify`: the 96-byte G2 infinity point as the signature, and an empty tail (no pubkey/msg pairs).
- For `bls_pairing_identity`: an empty argument list.

The operator returns `Ok(Reduction(cost, nil))` — indistinguishable from a legitimate successful verification. The corrupted result is the `Reduction` value: a successful execution that should have been a `BLSVerifyFailed` or `BLSPairingIdentityFailed` error. Any coin locked by such a puzzle can be spent without a valid signature.

---

### Likelihood Explanation

The likelihood depends on puzzle design. Puzzles that hardcode both the public key and the message are not vulnerable. However, puzzles that allow the spender to supply the `(pubkey, msg)` list — common in multi-sig, delegated-key, or aggregated-signature schemes — are directly exploitable. The attacker-controlled entry path is the CLVM solution bytes, which are fully attacker-controlled in any coin spend. The exploit requires only crafting a solution that routes the identity G2 point and an empty pair list to the operator.

---

### Recommendation

Add a minimum-count guard at the start of both operators. For `op_bls_verify`, require at least one `(pubkey, msg)` pair after the signature argument. For `op_bls_pairing_identity`, require at least one `(G1, G2)` pair. Example for `op_bls_verify`:

```rust
args = rest(a, args)?;
if nilp(a, args) {
    return Err(EvalErr::InvalidOpArg(
        input,
        "bls_verify requires at least one (pubkey, msg) pair".to_string(),
    ));
}
```

Puzzle authors who intentionally rely on the zero-pair identity behavior should be required to express that intent explicitly rather than having it be a silent default.

---

### Proof of Concept

The repository's own test suite documents the exact trigger:

**`op_bls_verify` zero-pair bypass** (`op-tests/test-bls-ops.txt`, line 351):
```
bls_verify 0xc00000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000 => 0 | 3000000
```
The 96-byte `0xc000…0000` is the compressed G2 infinity point (identity element). No pubkey or message is provided. The operator returns success.

**`op_bls_pairing_identity` zero-pair bypass** (`op-tests/test-bls-ops.txt`, line 305):
```
bls_pairing_identity => 0 | 3000000
```
Zero arguments. The operator returns success.

**Root cause** (`src/bls_ops.rs`):

`op_bls_verify` missing minimum pair count check: [1](#0-0) 

`op_bls_pairing_identity` missing minimum pair count check: [2](#0-1) 

Confirmed test vectors: [3](#0-2) [4](#0-3)

### Citations

**File:** src/bls_ops.rs (L342-357)
```rust
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

**File:** src/bls_ops.rs (L378-401)
```rust
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

**File:** op-tests/test-bls-ops.txt (L304-306)
```text
; identity
bls_pairing_identity => 0 | 3000000

```

**File:** op-tests/test-bls-ops.txt (L350-352)
```text
; identity (no messages signed)
bls_verify 0xc00000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000 => 0 | 3000000

```
