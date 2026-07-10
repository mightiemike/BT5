### Title
Deprecated `verify_transaction_inclusion` Remains Publicly Callable, Bypassing 64-Byte Merkle Forgery Protection - (File: `contract/src/lib.rs`)

### Summary

`verify_transaction_inclusion` is still a live, publicly callable NEAR method despite being deprecated. The security fix for the 64-byte transaction Merkle proof forgery vulnerability was placed exclusively in `verify_transaction_inclusion_v2`. Any unprivileged NEAR caller can invoke the old function directly, bypassing the coinbase Merkle proof validation entirely and obtaining a `true` verification result for a transaction that was never included in a Bitcoin block.

### Finding Description

The contract exposes two transaction-inclusion verification paths:

**Path A — `verify_transaction_inclusion` (deprecated, still callable):** [1](#0-0) 

This function computes a Merkle root from the caller-supplied `tx_id`, `tx_index`, and `merkle_proof`, then compares it to the stored block's `merkle_root`. It has no coinbase anchor check.

**Path B — `verify_transaction_inclusion_v2` (the fixed path):** [2](#0-1) 

This function first validates a coinbase Merkle proof of the **same length** as the transaction proof, anchoring the tree depth. Only after that check passes does it delegate to Path A.

The `#[deprecated]` attribute in Rust is a **compiler lint**, not a runtime access restriction. The function remains a public `#[near]` method and is callable by any account on-chain. The contract's own code acknowledges this:

```rust
#[allow(deprecated)]
self.verify_transaction_inclusion(args.into())
``` [3](#0-2) 

The underlying Merkle computation in `compute_root_from_merkle_proof` is position-driven and has no concept of tree depth: [4](#0-3) 

The 64-byte attack (documented at https://www.bitmex.com/blog/64-Byte-Transactions) exploits this: an internal Merkle tree node is itself the double-SHA256 of two 32-byte child hashes — exactly 64 bytes. A crafted 64-byte transaction whose `txid` equals an internal node hash can be "proven" to exist in a block by supplying a proof path that terminates one level above the real leaf layer. Without the coinbase depth anchor, `compute_root_from_merkle_proof` will compute the correct `merkle_root` and the function returns `true`.

The only guard in Path A against an empty proof is: [5](#0-4) 

A non-empty but shortened proof (one element fewer than the real tree depth) is not rejected.

### Impact Explanation

A downstream NEAR contract (e.g., an atomic swap or bridged-asset contract) that calls `verify_transaction_inclusion` receives a `true` result for a Bitcoin transaction that does not exist. This corrupts the canonical proof result the light client is designed to provide, enabling:

- Fraudulent release of bridged assets without a real Bitcoin deposit transaction.
- False confirmation of atomic swap settlement, allowing double-spend on the NEAR side.

The corrupted value is the **proof result** (`bool`) returned to the consumer contract, which is the sole on-chain source of truth for Bitcoin transaction inclusion.

### Likelihood Explanation

- No privileged role is required; any NEAR account can call `verify_transaction_inclusion`.
- The 64-byte Merkle forgery attack is publicly documented and has known construction techniques.
- The attacker only needs a valid block hash already in the contract's main chain and knowledge of that block's Merkle tree structure (publicly available from any Bitcoin node).
- The deprecated function is not gated by `#[pause]` in a way that would prevent this — it carries `#[pause]` but the contract is not paused in normal operation.

### Recommendation

Remove `verify_transaction_inclusion` from the public ABI entirely, or gate it with an access-control role that prevents unprivileged callers from invoking it. Since `verify_transaction_inclusion_v2` already delegates to it internally (with `#[allow(deprecated)]`), the internal logic can be preserved as a private helper while the public entry point is eliminated. Alternatively, add the coinbase proof length check directly inside `verify_transaction_inclusion` so both paths are equally protected.

### Proof of Concept

1. Identify a Bitcoin block already accepted into the contract's main chain (e.g., block hash `B`).
2. Obtain the block's full transaction list and Merkle tree from a public Bitcoin node.
3. Select an internal Merkle node `N` at depth `d-1` (one level above the leaves). `N` is the double-SHA256 of two 32-byte child hashes — a 64-byte preimage.
4. Construct a crafted `tx_id = N` and a `merkle_proof` of length `d-1` (one element shorter than a real leaf proof) that correctly reconstructs `merkle_root` from `N`.
5. Call `verify_transaction_inclusion` with `tx_id=N`, `tx_block_blockhash=B`, `tx_index=<index of N's subtree>`, `merkle_proof=<shortened proof>`, `confirmations=1`.
6. `compute_root_from_merkle_proof(N, index, proof)` returns the real `merkle_root`; the function returns `true` for a transaction that never existed.
7. Calling `verify_transaction_inclusion_v2` with the same arguments would fail at the coinbase proof length check, demonstrating that Path A is the bypass. [6](#0-5) [4](#0-3)

### Citations

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
