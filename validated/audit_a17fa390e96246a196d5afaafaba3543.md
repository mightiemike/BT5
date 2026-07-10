### Title
Deprecated `verify_transaction_inclusion` Remains Unrestricted and Callable, Enabling Proof-Verification Forgery — (File: `contract/src/lib.rs`)

---

### Summary

The deprecated `verify_transaction_inclusion` function is still a live, publicly callable NEAR method with no access control. It omits coinbase Merkle proof validation, making it vulnerable to the 64-byte transaction attack. Any unprivileged NEAR account can supply a crafted `tx_id` that is an internal Merkle tree node hash, receive a `true` return value, and present that result to any downstream contract that consumes it to authorize cross-chain asset releases or other high-value operations.

---

### Finding Description

`verify_transaction_inclusion` is decorated `#[deprecated]` and carries an explicit code-level warning, but the Rust `deprecated` attribute is a compile-time lint only — it does not prevent runtime invocation. The function remains a fully reachable public method on the deployed contract. [1](#0-0) 

The function's own documentation states:

> "This function may return `true` if the provided `tx_id` is a hash of an internal node in the Merkle tree rather than a valid transaction hash." [2](#0-1) 

The verification logic reduces entirely to:

```
compute_root_from_merkle_proof(tx_id, tx_index, &merkle_proof) == header.block_header.merkle_root
``` [3](#0-2) 

`compute_root_from_merkle_proof` is a pure hash-chain computation with no leaf-vs-internal-node distinction: [4](#0-3) 

There is no coinbase proof check, no role guard (`#[trusted_relayer]` is absent), and no `#[pause]` bypass restriction beyond the standard pause flag. Any NEAR account can call it freely.

The safe replacement, `verify_transaction_inclusion_v2`, adds a coinbase proof check before delegating back to the deprecated function: [5](#0-4) 

But the deprecated path remains open in parallel, so the coinbase guard in v2 is trivially bypassed by calling v1 directly.

---

### Impact Explanation

The broken invariant is: *`verify_transaction_inclusion` returns `true` only when `tx_id` is the hash of a real leaf transaction included in the identified block.*

An attacker who knows the Merkle tree of any mainchain block can identify an internal node hash at a known position. Supplying that hash as `tx_id` with the correct sibling path causes the function to return `true` for a Bitcoin transaction that does not exist. Any downstream NEAR contract that gates an asset release, bridge withdrawal, or authorization decision on this return value is deceived into executing that action without a corresponding on-chain Bitcoin event.

---

### Likelihood Explanation

- The function is publicly callable with no authentication.
- The 64-byte transaction attack is publicly documented (referenced in the v2 docstring itself: https://www.bitmex.com/blog/64-Byte-Transactions).
- Mainchain block Merkle trees are fully public; an attacker needs only to read a block and compute an internal node position.
- No special tooling, privileged key, or social engineering is required.

Likelihood: **High**.

---

### Recommendation

- **Short term:** Remove `verify_transaction_inclusion` from the contract entirely, or add an `#[access_control]` guard that prevents any external caller from invoking it. Document the removal explicitly.
- **Long term:** Audit all public methods for deprecated or unsafe paths that remain reachable. Enforce that deprecated functions are either deleted or gated behind a role that no external account holds.

---

### Proof of Concept

1. Identify any block hash `B` present in `mainchain_header_to_height` (publicly readable via `get_block_hash_by_height`).
2. Obtain the full transaction list for block `B` from a Bitcoin node. Compute the Merkle tree. Select any internal node `N` at tree level `L`, position `P`.
3. Construct the sibling path `proof` from `N` up to the Merkle root (length `L`).
4. Call on the NEAR contract:
   ```
   verify_transaction_inclusion({
     tx_id: N,               // internal node hash, not a real txid
     tx_block_blockhash: B,
     tx_index: P * 2^L,     // leaf-equivalent index for this subtree position
     merkle_proof: proof,
     confirmations: 1
   })
   ```
5. The function returns `true`. No Bitcoin transaction with id `N` exists.
6. Present this `true` result to any downstream bridge or escrow contract that calls `verify_transaction_inclusion` to authorize a withdrawal. The withdrawal executes without a real Bitcoin transaction.

### Citations

**File:** contract/src/lib.rs (L276-280)
```rust
    ///
    /// # Warning
    /// This function may return `true` if the provided `tx_id` is a hash of an internal node in the Merkle tree rather than a valid transaction hash.
    /// We assume that validation of whether the `tx_id` corresponds to a valid transaction hash is performed at a higher level of verification.
    ///
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

**File:** contract/src/lib.rs (L317-323)
```rust
        // compute merkle tree root and check if it matches block's original merkle tree root
        merkle_tools::compute_root_from_merkle_proof(
            args.tx_id,
            usize::try_from(args.tx_index).unwrap(),
            &args.merkle_proof,
        ) == header.block_header.merkle_root
    }
```

**File:** contract/src/lib.rs (L347-369)
```rust
    pub fn verify_transaction_inclusion_v2(&self, #[serializer(borsh)] args: ProofArgsV2) -> bool {
        require!(
            args.merkle_proof.len() == args.coinbase_merkle_proof.len(),
            "Coinbase merkle proof and transaction merkle proof should have the same length"
        );

        let header = self
            .headers_pool
            .get(&args.tx_block_blockhash)
            .unwrap_or_else(|| env::panic_str("cannot find requested transaction block"));

        require!(
            merkle_tools::compute_root_from_merkle_proof(
                args.coinbase_tx_id.clone(),
                0usize,
                &args.coinbase_merkle_proof,
            ) == header.block_header.merkle_root,
            "Incorrect coinbase merkle proof"
        );

        #[allow(deprecated)]
        self.verify_transaction_inclusion(args.into())
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
