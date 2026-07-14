### Title
`op_bls_verify` Accepts Zero (pubkey, message) Pairs and Returns Success, Enabling Signature Verification Bypass — (`src/bls_ops.rs`)

---

### Summary

`op_bls_verify` in `src/bls_ops.rs` does not enforce a minimum count of `(pubkey, message)` pairs. When invoked with only the G2 identity point as the signature and no pairs, `aggregate_verify` is called with an empty items list and returns `true` by BLS mathematical definition. Any Chialisp coin that uses `bls_verify` with an attacker-controlled number of signers can be completely bypassed by providing the G2 identity point and zero pairs.

---

### Finding Description

`op_bls_verify` expects the argument list `G2 G1 msg G1 msg ...`: a BLS signature (G2) followed by one or more `(G1 pubkey, message)` pairs. The function parses the signature unconditionally, then collects pairs into `items` via a `while !nilp(...)` loop. If the argument list terminates immediately after the signature, `items` remains empty and `aggregate_verify(&signature, items)` is called with an empty vector.

```rust
// src/bls_ops.rs, op_bls_verify
let mut items = Vec::<(PublicKey, Atom)>::new();
while !nilp(a, args) {          // ← loop body never executes if no pairs follow
    ...
    items.push((pk, msg));
}

if !aggregate_verify(&signature, items) {   // ← called with items = []
    Err(EvalErr::BLSVerifyFailed(input))?
} else {
    Ok(Reduction(cost, a.nil()))            // ← returns success
}
```

BLS aggregate verify over an empty message set is defined to return `true` when the signature is the G2 identity element (`0xc000...0000`). This is not a library bug — it is the correct mathematical result — but `op_bls_verify` exposes it as a reachable success path with no guard.

The operator test vectors in `op-tests/test-bls-ops.txt` explicitly confirm this behavior:

```
; identity (no messages signed)
bls_verify 0xc000...0000 => 0 | 3000000
```

The operator returns `0` (success) with cost `3000000` when given only the G2 identity point and no pairs.

The same structural defect exists in `op_bls_pairing_identity`:

```rust
// src/bls_ops.rs, op_bls_pairing_identity
let mut items = Vec::<(G1Element, G2Element)>::new();
while !nilp(a, args) { ... }   // ← never executes if args is nil
if !aggregate_pairing(items) { ... }
else { Ok(Reduction(cost, a.nil())) }  // ← returns success with empty items
```

Confirmed by the test vector:
```
; identity
bls_pairing_identity => 0 | 3000000
```

---

### Impact Explanation

Any Chialisp coin puzzle that uses `bls_verify` with an attacker-controlled number of `(pubkey, message)` pairs — for example, a multisig coin where the signer list is drawn from the solution — can be fully bypassed. The attacker submits a spend bundle where the solution provides the G2 identity point as the signature and an empty signer list. `op_bls_verify` returns success without verifying any cryptographic relationship between the signature and any public key or message. The broken invariant is: **`op_bls_verify` is supposed to attest that a signature covers at least one authorized (pubkey, message) pair; with zero pairs it attests nothing**.

Because `clvm_rs` is the consensus execution engine used by all Chia full nodes, this behavior is deterministic and network-wide: every node would accept the spend as valid.

---

### Likelihood Explanation

The likelihood is **medium**. The attack requires a coin puzzle that passes the signer list from the solution into `bls_verify` rather than hardcoding pubkeys and messages in the puzzle itself. This pattern is natural for multisig or threshold-signature coins where the set of signers is dynamic. A puzzle author who is unaware that `bls_verify` succeeds with zero pairs would not add a separate length guard, leaving the coin fully drainable by any spender who provides the G2 identity point.

---

### Recommendation

Add a minimum-count guard in `op_bls_verify` before calling `aggregate_verify`:

```rust
if items.is_empty() {
    return Err(EvalErr::InvalidOpArg(
        input,
        "bls_verify requires at least one (pubkey, message) pair".to_string(),
    ));
}
```

Apply the same guard in `op_bls_pairing_identity`:

```rust
if items.is_empty() {
    return Err(EvalErr::InvalidOpArg(
        input,
        "bls_pairing_identity requires at least one (G1, G2) pair".to_string(),
    ));
}
```

Update the test vectors in `op-tests/test-bls-ops.txt` to expect `FAIL` for the zero-argument cases.

---

### Proof of Concept

**Attacker-controlled CLVM bytes** (pseudocode):

```
; Coin puzzle (victim): enforces that a BLS signature covers a dynamic signer list
(mod (sig . pairs)
  (bls_verify sig . pairs))

; Attacker solution:
; sig  = G2 identity point (0xc000...0000, 96 bytes)
; pairs = () (empty list)
(0xc000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000)
```

Execution path:
1. `op_bls_verify` is dispatched with `input` = `(G2_identity . ())`.
2. `signature` = G2 identity element (valid G2 point, parses without error).
3. `args = rest(...)` = `nil`.
4. `while !nilp(a, args)` — loop body never executes; `items = []`.
5. `aggregate_verify(&G2_identity, [])` → `true` (BLS identity property).
6. `Ok(Reduction(3000000, nil()))` — spend accepted.

**Confirmed by existing test vector** (`op-tests/test-bls-ops.txt`, line 351):

```
; identity (no messages signed)
bls_verify 0xc00000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000 => 0 | 3000000
``` [1](#0-0) [2](#0-1) [3](#0-2) [4](#0-3) [5](#0-4)

### Citations

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

**File:** op-tests/test-bls-ops.txt (L304-305)
```text
; identity
bls_pairing_identity => 0 | 3000000
```

**File:** op-tests/test-bls-ops.txt (L350-351)
```text
; identity (no messages signed)
bls_verify 0xc00000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000 => 0 | 3000000
```
