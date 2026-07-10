### Title
Deprecated `verify_transaction_inclusion` Remains a Live, Callable Endpoint Vulnerable to 64-Byte Merkle Proof Forgery - (`contract/src/lib.rs`)

### Summary

The contract exposes two transaction-inclusion verification endpoints. `verify_transaction_inclusion_v2` was introduced specifically to mitigate the 64-byte Merkle proof forgery attack. However, the original `verify_transaction_inclusion` was never removed or access-restricted — it remains a fully callable public function. Any unprivileged NEAR caller can invoke it directly, bypassing the coinbase-proof protection entirely and obtaining a `true` result for a forged transaction inclusion claim.

### Finding Description

`verify_transaction_inclusion` is marked `#[deprecated]` in Rust source, but `#[deprecated]` is a **compile-time lint only** — it does not restrict runtime callability. The function is still decorated with `#[pause]` (not `#[private]`) and is part of the contract's public ABI. [1](#0-0) 

The function's own documentation explicitly states the vulnerability it carries: [2](#0-1) 

`verify_transaction_inclusion_v2` was introduced to close this gap by requiring a coinbase Merkle proof at index 0, which prevents an attacker from substituting an internal Merkle tree node as a leaf: [3](#0-2) 

But because v1 is still callable directly, the coinbase-proof guard in v2 is entirely bypassable. The underlying `compute_root_from_merkle_proof` function used by both versions performs no validation that the supplied hash corresponds to a real transaction — it only checks that the computed root matches the stored `merkle_root`: [4](#0-3) 

### Impact Explanation

An attacker who knows the transaction set of any mainchain block can compute internal Merkle tree nodes (which are SHA256d hashes of 64-byte concatenations of child hashes). They can then supply such an internal node as `tx_id` along with a crafted sibling path to `verify_transaction_inclusion`. The function will compute a root that matches the stored `merkle_root` and return `true`, falsely asserting that a non-existent transaction was included in the block.

Any downstream protocol (bridge, cross-chain application, or recipient contract) that calls `verify_transaction_inclusion` and acts on a `true` result will accept a forged proof of transaction inclusion. This is a **proof-verification forgery** with direct financial impact in any bridge or SPV-based settlement context.

### Likelihood Explanation

The attack requires no privileged role, no leaked keys, and no social engineering. The 64-byte transaction Merkle forgery technique is publicly documented (https://www.bitmex.com/blog/64-Byte-Transactions) and well understood. The attacker only needs to:
1. Identify any block in the contract's mainchain.
2. Compute an internal Merkle node from that block's known transaction tree.
3. Construct a valid sibling path for that node.
4. Call `verify_transaction_inclusion` directly.

The entry path is fully permissionless. Likelihood is **medium-high** for any deployment where the contract's verification result is consumed by a downstream protocol.

### Recommendation

Remove `verify_transaction_inclusion` from the contract's public ABI entirely, or restrict it with `#[private]` so it cannot be called externally. The `#[deprecated]` attribute provides no runtime protection. All callers must be migrated to `verify_transaction_inclusion_v2`. [5](#0-4) 

### Proof of Concept

1. Deploy the contract (Bitcoin feature) with a known genesis and several real mainchain blocks.
2. Pick any block `B` in the mainchain whose full transaction set is known.
3. Compute the Merkle tree of `B`'s transactions. Select any internal node `N` at depth `d` (a 32-byte SHA256d hash of two 64-byte child concatenations).
4. Construct a `merkle_proof` of length `d` that walks from `N` up to the root, using the sibling hashes at each level.
5. Call `verify_transaction_inclusion` with:
   - `tx_id = N` (the internal node, not a real txid)
   - `tx_block_blockhash = B`
   - `tx_index` = the position of `N` in the tree at depth `d`
   - `merkle_proof` = the sibling path constructed above
   - `confirmations = 1`
6. The function returns `true`, falsely asserting that `N` is a confirmed transaction in block `B`. [6](#0-5)

### Citations

**File:** contract/src/lib.rs (L276-280)
```rust
    ///
    /// # Warning
    /// This function may return `true` if the provided `tx_id` is a hash of an internal node in the Merkle tree rather than a valid transaction hash.
    /// We assume that validation of whether the `tx_id` corresponds to a valid transaction hash is performed at a higher level of verification.
    ///
```

**File:** contract/src/lib.rs (L283-323)
```rust
    #[deprecated(
        since = "0.5.0",
        note = "Use `verify_transaction_inclusion_v2` instead."
    )]
    #[pause]
    pub fn verify_transaction_inclusion(&self, #[serializer(borsh)] args: ProofArgs) -> bool {
        require!(
            args.confirmations <= self.gc_threshold,
            "The required number of confirmations exceeds the number of blocks stored in memory"
        );

        let heaviest_block_header = self
            .headers_pool
            .get(&self.mainchain_tip_blockhash)
            .unwrap_or_else(|| env::panic_str(ERR_KEY_NOT_EXIST));
        let target_block_height = self
            .mainchain_header_to_height
            .get(&args.tx_block_blockhash)
            .unwrap_or_else(|| env::panic_str("block does not belong to the current main chain"));

        // Check requested confirmations. No need to compute proof if insufficient confirmations.
        require!(
            (heaviest_block_header.block_height).saturating_sub(target_block_height) + 1
                >= args.confirmations,
            "Not enough blocks confirmed"
        );

        let header = self
            .headers_pool
            .get(&args.tx_block_blockhash)
            .unwrap_or_else(|| env::panic_str("cannot find requested transaction block"));

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
