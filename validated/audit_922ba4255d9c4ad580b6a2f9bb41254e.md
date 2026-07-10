### Title
Unvalidated `tx_id` Input in `verify_transaction_inclusion` Enables Merkle Internal-Node Forgery — (`contract/src/lib.rs`)

---

### Summary

`verify_transaction_inclusion` accepts a caller-supplied `tx_id` without validating that it represents a genuine leaf-level transaction hash. Any unprivileged NEAR caller can supply an internal Merkle tree node as `tx_id`, construct a valid sibling proof path, and receive a `true` return value for a transaction that does not exist. This is the direct analog of the XSS finding: attacker-controlled data is consumed by the contract without sanitization, corrupting the verification result that downstream consumers rely on.

---

### Finding Description

`verify_transaction_inclusion` computes a Merkle root from the caller-supplied `tx_id` and `merkle_proof` and compares it to the stored block header's `merkle_root`: [1](#0-0) 

The only guard on the proof inputs is: [2](#0-1) 

There is no check that `tx_id` is a leaf-level transaction hash rather than an internal Merkle node. `compute_root_from_merkle_proof` is a pure hash-chain computation: [3](#0-2) 

It accepts any 32-byte value as the starting hash and walks up the tree. An internal node at depth *d* already has a valid sibling path of length *d* to the root, so the function returns the correct `merkle_root` when given that internal node as `tx_id`.

The function carries an explicit `#[deprecated]` annotation and a code-level warning: [4](#0-3) 

However, `#[deprecated]` in Rust is a compile-time lint only; it does not prevent runtime invocation. The function remains callable by any NEAR account because it has no role guard and no `#[trusted_relayer]` attribute: [5](#0-4) 

`verify_transaction_inclusion_v2` is the intended replacement and adds a coinbase proof length check: [6](#0-5) 

But the old entry point is never removed or access-controlled, so the unvalidated path remains open.

---

### Impact Explanation

A downstream contract or off-chain application that calls `verify_transaction_inclusion` to gate fund release or cross-chain state transitions receives a `true` result for a fabricated transaction. The attacker does not need to mine any block or control any privileged role; they only need to know the Merkle tree structure of any block already stored in the contract's `headers_pool`. The corrupted canonical value is the SPV proof result: a `bool` that is supposed to be a cryptographic guarantee of transaction inclusion but is not.

---

### Likelihood Explanation

The attack requires only:
1. Identifying any block in the mainchain with two or more transactions (all real Bitcoin blocks qualify).
2. Computing an internal Merkle node and its sibling path — both are derivable from public block data.
3. Calling `verify_transaction_inclusion` with the internal node as `tx_id`.

No privileged access, no key material, and no social engineering are required. The function is publicly callable on a live NEAR deployment.

---

### Recommendation

Remove `verify_transaction_inclusion` entirely, or add an explicit access-control role (e.g., `Role::DAO`) so it cannot be called by unprivileged accounts. All callers should be migrated to `verify_transaction_inclusion_v2`. If backward compatibility is required during a transition period, add a `require!` that the `merkle_proof` length is strictly greater than zero **and** that `tx_id` is not equal to any value derivable from a known internal-node position — or, more robustly, enforce that the proof depth implies a minimum tree size that rules out single-node forgeries.

---

### Proof of Concept

1. Identify any block stored in the contract's mainchain, e.g., at height `H`. Retrieve its `merkle_root` and the full transaction list from a Bitcoin node.
2. Compute the internal Merkle node `N` at depth 1 (the hash of `tx[0]` and `tx[1]`).
3. Construct the sibling proof path from `N` up to `merkle_root` (length = tree depth − 1).
4. Call on NEAR:
   ```
   verify_transaction_inclusion({
     tx_id: N,                        // internal node, not a real txid
     tx_block_blockhash: <block hash at H>,
     tx_index: 0,                     // position consistent with the proof path
     merkle_proof: [<siblings from N to root>],
     confirmations: 1
   })
   ```
5. The contract returns `true`. No such transaction exists on-chain.

### Citations

**File:** contract/src/lib.rs (L276-288)
```rust
    ///
    /// # Warning
    /// This function may return `true` if the provided `tx_id` is a hash of an internal node in the Merkle tree rather than a valid transaction hash.
    /// We assume that validation of whether the `tx_id` corresponds to a valid transaction hash is performed at a higher level of verification.
    ///
    /// # Panics
    /// Multiple cases
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
