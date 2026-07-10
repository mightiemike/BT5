### Title
Coinbase Proof Validation Enforced Only in `verify_transaction_inclusion_v2`, Bypassed via Direct Call to Deprecated `verify_transaction_inclusion` — (File: `contract/src/lib.rs`)

---

### Summary

The coinbase Merkle proof check — introduced to prevent the 64-byte transaction Merkle proof forgery attack — is enforced only in `verify_transaction_inclusion_v2` but is entirely absent from `verify_transaction_inclusion` (v1). The v1 function remains a publicly callable NEAR contract method with no access control. Any unprivileged caller can invoke v1 directly, bypassing the anti-forgery constraint and obtaining a `true` SPV verification result for a transaction that was never included in a Bitcoin block.

---

### Finding Description

`verify_transaction_inclusion_v2` (line 347) enforces a coinbase Merkle proof check before delegating to v1:

```rust
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
```

`verify_transaction_inclusion` (v1, line 288) performs no such check. It accepts an arbitrary `tx_id` and a `merkle_proof`, computes the Merkle root, and returns `true` if it matches the stored block's `merkle_root`:

```rust
merkle_tools::compute_root_from_merkle_proof(
    args.tx_id,
    usize::try_from(args.tx_index).unwrap(),
    &args.merkle_proof,
) == header.block_header.merkle_root
```

The function is annotated `#[deprecated]` in Rust source, but `#[deprecated]` is a compiler lint — it does not remove the method from the compiled WASM ABI. The function carries `#[pause]` (not `#[private]`), so it remains callable by any NEAR account when the contract is unpaused. The contract's own doc comment acknowledges the risk explicitly:

> "This function may return `true` if the provided `tx_id` is a hash of an internal node in the Merkle tree rather than a valid transaction hash."

The constraint (coinbase proof validation) is enforced in `verify_transaction_inclusion_v2` but not in `verify_transaction_inclusion`, creating an identical structural gap to the reported LP share enforcement bypass.

---

### Impact Explanation

The 64-byte transaction Merkle proof forgery (documented at https://www.bitmex.com/blog/64-Byte-Transactions) allows an attacker to supply a 64-byte internal Merkle tree node as `tx_id`. Because Bitcoin's Merkle tree uses the same double-SHA256 hash for both leaf nodes (transactions) and internal nodes, a crafted internal node can be presented as a valid `tx_id` with a proof that correctly reconstructs the block's `merkle_root`. The contract returns `true` for a Bitcoin transaction that does not exist.

Any downstream NEAR contract or application that calls `verify_transaction_inclusion` to gate a cross-chain action — releasing bridged funds, minting wrapped tokens, or confirming a payment — will be deceived into accepting a forged proof. This is a direct proof-verification forgery with concrete financial impact on integrators.

---

### Likelihood Explanation

The attack requires no privileged access. The attacker needs only:
1. A real Bitcoin block already stored in the contract's `mainchain_header_to_height` map (obtainable via `get_last_block_header` or `get_block_hash_by_height`).
2. Knowledge of two adjacent transaction hashes in that block's Merkle tree (publicly available from any Bitcoin block explorer).
3. The ability to call `verify_transaction_inclusion` on NEAR — open to any account.

The 64-byte forgery technique is well-documented, requires no cryptographic break, and has been exploited in practice against SPV clients. The entry path is fully reachable by an unprivileged caller.

---

### Recommendation

Remove `verify_transaction_inclusion` from the public WASM ABI. The simplest fix is to change its visibility from `pub fn` to `pub(crate) fn`, which eliminates it from the on-chain interface while preserving its use as an internal helper called by `verify_transaction_inclusion_v2`. Alternatively, add the same coinbase proof validation to v1 before the Merkle root comparison.

---

### Proof of Concept

1. Query the contract for a known mainchain block hash: call `get_block_hash_by_height(H)` for any stored height `H`.
2. From a Bitcoin block explorer, retrieve two adjacent transaction hashes `tx_a` and `tx_b` at Merkle leaf positions `2k` and `2k+1` in that block.
3. Compute `internal_node = SHA256d(tx_a || tx_b)` — this is a 32-byte value that is a valid internal Merkle node.
4. Construct a `merkle_proof` that, starting from `internal_node` at index `k` in the next Merkle level, correctly reconstructs the block's `merkle_root` using the remaining sibling hashes (all publicly available).
5. Call `verify_transaction_inclusion` with `tx_id = internal_node`, `tx_block_blockhash = <block hash from step 1>`, `tx_index = k`, and the proof from step 4.
6. The contract returns `true`. No such transaction exists in the Bitcoin block. [1](#0-0) [2](#0-1)

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

**File:** contract/src/lib.rs (L346-369)
```rust
    #[pause]
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
