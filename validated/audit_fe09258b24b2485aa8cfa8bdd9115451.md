### Title
Deprecated `verify_transaction_inclusion` Remains a Live Public Endpoint, Enabling 64-Byte Merkle Proof Forgery — (File: `contract/src/lib.rs`)

---

### Summary

`verify_transaction_inclusion` is marked `#[deprecated]` but is still a fully callable public NEAR function. The Rust `#[deprecated]` attribute is a compile-time lint only — it imposes no runtime restriction. Any unprivileged NEAR caller can invoke it directly and supply a crafted `tx_id` that is an internal Merkle tree node rather than a real transaction hash. The function will return `true`, falsely asserting that a non-existent Bitcoin transaction is included in a confirmed block. Recipient contracts that consume this result are deceived in the same way a mail recipient is deceived by a spoofed sender identity.

---

### Finding Description

`verify_transaction_inclusion` carries an explicit self-documenting warning:

> *"This function may return `true` if the provided `tx_id` is a hash of an internal node in the Merkle tree rather than a valid transaction hash."* [1](#0-0) 

The function was deprecated in favour of `verify_transaction_inclusion_v2`, which adds a coinbase proof check to mitigate the 64-byte forgery. However, the deprecated function was never removed or access-gated — it retains `#[pause]` (active only when the contract is paused, which is not the default) and no other restriction: [2](#0-1) 

The verification logic itself performs no check that `tx_id` is a leaf-level hash. It computes `compute_root_from_merkle_proof(tx_id, tx_index, merkle_proof)` and compares the result to the stored `merkle_root`: [3](#0-2) 

`compute_root_from_merkle_proof` is a pure hash-chain computation with no leaf-vs-internal-node distinction: [4](#0-3) 

The 64-byte forgery (documented at https://www.bitmex.com/blog/64-Byte-Transactions) exploits the fact that Bitcoin's Merkle tree uses the same double-SHA256 for both leaf and internal nodes. An attacker can take any internal node `N = SHA256d(left_child || right_child)` from a real block's Merkle tree, treat it as a fake `tx_id`, and supply a proof path one level shorter than a real leaf proof. The computed root still equals the stored `merkle_root`, so the function returns `true`.

---

### Impact Explanation

Any recipient contract (bridge, payment verifier, cross-chain settlement layer) that calls `verify_transaction_inclusion` and acts on a `true` result can be made to process a Bitcoin transaction that never existed. The attacker controls the claimed `tx_id` entirely — it can encode any payload the attacker chooses (e.g., a fake payment to themselves). The broken invariant is: *a `true` return from the SPV endpoint must correspond to a real leaf-level Bitcoin transaction*. This invariant is violated without any privileged access.

**Impact: 4 / 10** — matches the external report's score. Financial loss is possible for any protocol that bridges or settles based on this proof result; the impact is bounded by the value locked in consuming contracts.

---

### Likelihood Explanation

The attack requires:
1. Knowledge of the 64-byte Merkle forgery technique (publicly documented).
2. A Bitcoin block already accepted by the contract (no block submission needed).
3. A direct NEAR function call — no privileged role, no key leak, no social engineering.

The entry path is fully permissionless. The only friction is constructing the correct internal-node proof, which is straightforward given the block's transaction list.

**Likelihood: 4 / 10** — the technique is known and the endpoint is open; exploitation is limited only by the attacker's motivation and the value of consuming contracts.

---

### Recommendation

**Remove or hard-gate the deprecated endpoint.** The `#[deprecated]` attribute does not prevent runtime calls. Options in order of preference:

1. **Remove `verify_transaction_inclusion` entirely** from the public ABI. Callers must migrate to `verify_transaction_inclusion_v2`.
2. If removal is not immediately possible, **add a runtime guard** that unconditionally panics:
   ```rust
   pub fn verify_transaction_inclusion(&self, ...) -> bool {
       env::panic_str("verify_transaction_inclusion is removed; use verify_transaction_inclusion_v2");
   }
   ```
3. At minimum, add an access-control role check so only trusted internal callers (e.g., `verify_transaction_inclusion_v2`) can reach the body — but option 1 or 2 is strongly preferred.

---

### Proof of Concept

**Setup**: A Bitcoin block `B` is in the contract's mainchain. Its Merkle tree has at least two transactions `T0` (coinbase) and `T1`, so the root is `R = SHA256d(T0 || T1)`.

**Forge a fake transaction**:
- Choose `fake_tx_id = R` (the Merkle root itself, which is the internal node one level above the leaves).
- Supply `tx_index = 0`, `merkle_proof = []` (empty — one level shorter than a real proof).

**Call**:
```
verify_transaction_inclusion({
    tx_id:             <R>,          // internal node, not a real tx
    tx_block_blockhash: <hash of B>,
    tx_index:          0,
    merkle_proof:      [],           // BLOCKED: "Merkle proof is empty" panic
    confirmations:     1,
})
```

The empty-proof guard at line 315 blocks the trivial case. A one-level-deeper forgery works instead:

- Let `T0, T1, T2, T3` be four transactions. Internal node `I_01 = SHA256d(T0||T1)`, `I_23 = SHA256d(T2||T3)`, root `R = SHA256d(I_01||I_23)`.
- Set `fake_tx_id = I_01`, `tx_index = 0`, `merkle_proof = [I_23]`.
- `compute_root_from_merkle_proof(I_01, 0, [I_23])` = `SHA256d(I_01 || I_23)` = `R` ✓ [5](#0-4) 

The function returns `true` for `fake_tx_id = I_01`, which is not a real transaction. Any consuming contract is now deceived into believing this fabricated transaction was confirmed in block `B`. [6](#0-5)

### Citations

**File:** contract/src/lib.rs (L278-279)
```rust
    /// This function may return `true` if the provided `tx_id` is a hash of an internal node in the Merkle tree rather than a valid transaction hash.
    /// We assume that validation of whether the `tx_id` corresponds to a valid transaction hash is performed at a higher level of verification.
```

**File:** contract/src/lib.rs (L283-288)
```rust
    #[deprecated(
        since = "0.5.0",
        note = "Use `verify_transaction_inclusion_v2` instead."
    )]
    #[pause]
    pub fn verify_transaction_inclusion(&self, #[serializer(borsh)] args: ProofArgs) -> bool {
```

**File:** contract/src/lib.rs (L315-323)
```rust
        require!(!args.merkle_proof.is_empty(), "Merkle proof is empty");

        // compute merkle tree root and check if it matches block's original merkle tree root
        merkle_tools::compute_root_from_merkle_proof(
            args.tx_id,
            usize::try_from(args.tx_index).unwrap(),
            &args.merkle_proof,
        ) == header.block_header.merkle_root
    }
```

**File:** merkle-tools/src/lib.rs (L34-52)
```rust
pub fn compute_root_from_merkle_proof(
    transaction_hash: H256,
    transaction_position: usize,
    merkle_proof: &Vec<H256>,
) -> H256 {
    let mut current_hash = transaction_hash;
    let mut current_position = transaction_position;

    for proof_hash in merkle_proof {
        if current_position % 2 == 0 {
            current_hash = compute_hash(&current_hash, proof_hash);
        } else {
            current_hash = compute_hash(proof_hash, &current_hash);
        }
        current_position /= 2;
    }

    current_hash
}
```
