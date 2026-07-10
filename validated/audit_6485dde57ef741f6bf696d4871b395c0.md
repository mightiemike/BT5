### Title
Caller-Supplied `tx_id` Not Validated as a Real Leaf Transaction — Merkle Proof Forgery via Internal Node — (File: `contract/src/lib.rs`)

---

### Summary

`verify_transaction_inclusion` accepts a caller-supplied `tx_id` and a Merkle proof and returns `true` if the proof reconstructs the block's Merkle root. It never validates that `tx_id` is a leaf node (a real transaction) rather than an internal Merkle tree node. Any unprivileged NEAR caller can supply an internal node hash as `tx_id` with a valid but shorter proof path, causing the function to certify the inclusion of a transaction that does not exist.

---

### Finding Description

`verify_transaction_inclusion` in `contract/src/lib.rs` performs the following steps:

1. Confirms the block is on the mainchain with sufficient confirmations.
2. Requires `merkle_proof` to be non-empty.
3. Calls `merkle_tools::compute_root_from_merkle_proof(args.tx_id, args.tx_index, &args.merkle_proof)` and compares the result to `header.block_header.merkle_root`. [1](#0-0) 

The function never checks whether `args.tx_id` is a leaf node. In a Bitcoin Merkle tree, every internal node at depth `D` is itself a valid 32-byte hash that can be "proven" with a proof of length `D` (the sibling hashes from depth `D` up to the root). An attacker who knows the Merkle tree of any real, confirmed block can:

1. Pick any internal node `N` at depth `D` (where `D < tree_depth`).
2. Construct a proof of length `D` consisting of the real sibling hashes at each level.
3. Call `verify_transaction_inclusion` with `tx_id = N`, `tx_index` set to the correct position, and the crafted proof.

`compute_root_from_merkle_proof` will reconstruct the correct Merkle root, and the function returns `true` — certifying inclusion of a transaction that does not exist. [2](#0-1) 

The code itself documents this broken invariant in a warning comment: [3](#0-2) 

The `#[deprecated]` attribute only emits a Rust compiler warning; it does **not** remove the function from the public ABI. Any NEAR account can still call it directly. [4](#0-3) 

`verify_transaction_inclusion_v2` mitigates this by requiring the coinbase proof and the transaction proof to have the same length, anchoring the claimed depth to the real tree depth. However, `verify_transaction_inclusion_v1` remains callable and unmitigated. [5](#0-4) 

---

### Impact Explanation

Any downstream contract or bridge that calls `verify_transaction_inclusion` to gate a high-value action (e.g., releasing funds, minting wrapped tokens, confirming a deposit) can be deceived into accepting a forged proof. The attacker does not need to forge a Bitcoin block or break any cryptographic primitive — they only need to know the Merkle tree of any real, sufficiently confirmed block, which is public information. The function returns `true` for a `tx_id` that corresponds to no real Bitcoin transaction.

This is the direct analog of the external report: just as the `mint` function did not bind the recipient address to the original PEG-IN transaction, `verify_transaction_inclusion` does not bind `tx_id` to a real leaf transaction — allowing a caller to substitute an arbitrary internal node and receive a valid inclusion certificate.

---

### Likelihood Explanation

The entry path requires no privileges: any NEAR account can call `verify_transaction_inclusion` with Borsh-serialized `ProofArgs`. The Merkle tree of every Bitcoin block is public. Constructing the attack requires only reading a block's transaction list and computing sibling hashes — standard Bitcoin SPV tooling. The only precondition is that the target block is on the mainchain with the required number of confirmations, which is trivially satisfiable for any old confirmed block.

---

### Recommendation

1. **Remove `verify_transaction_inclusion` from the public ABI** or gate it behind a role check so it cannot be called by unprivileged accounts. Deprecation alone is insufficient.
2. Alternatively, apply the same coinbase-proof-length check from `verify_transaction_inclusion_v2` to the v1 path, or redirect all callers to v2.
3. Consumers of the verification result should be explicitly documented to use only `verify_transaction_inclusion_v2`.

---

### Proof of Concept

Given a real confirmed Bitcoin block `B` with Merkle root `R` and transactions `[T0, T1, T2, T3]`:

```
         R
        / \
      I01  I23
      / \  / \
     T0 T1 T2 T3
```

The internal node `I01 = H(T0 || T1)` exists at depth 1. An attacker constructs:

- `tx_id = I01`
- `tx_index = 0` (position of `I01` at depth 1)
- `merkle_proof = [I23]` (length 1, the sibling at depth 1)

`compute_root_from_merkle_proof(I01, 0, [I23])` computes `H(I01 || I23) = R`, which matches the block's Merkle root. `verify_transaction_inclusion` returns `true`. [6](#0-5) 

No real transaction with txid `I01` exists. Any bridge or consumer contract that trusts this return value to authorize a payout or mint would be exploited.

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

**File:** btc-types/src/contract_args.rs (L16-24)
```rust
#[near(serializers = [borsh, json])]
#[derive(Clone, Debug)]
pub struct ProofArgs {
    pub tx_id: H256,
    pub tx_block_blockhash: H256,
    pub tx_index: u64,
    pub merkle_proof: Vec<H256>,
    pub confirmations: u64,
}
```
