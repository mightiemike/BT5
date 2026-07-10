### Title
Deprecated `verify_transaction_inclusion` Remains Callable, Bypassing Coinbase Merkle Proof Validation and Enabling 64-Byte Transaction Forgery — (`contract/src/lib.rs`)

---

### Summary

`verify_transaction_inclusion` (v1) is still a live, unpermissioned public entry point on the contract. It omits the coinbase Merkle proof check that `verify_transaction_inclusion_v2` mandates. An unprivileged NEAR caller can supply a crafted `tx_id` that is an internal Merkle-tree node rather than a real transaction hash and receive a `true` proof result, without ever going through the security step that was introduced to close the 64-byte forgery vector. This is the direct analog of the external report: a caller obtains the benefit (a `true` verification result) while bypassing the mandatory state-validation step (coinbase proof check), leaving downstream consumers with a corrupted proof result.

---

### Finding Description

`verify_transaction_inclusion_v2` was introduced specifically to close the 64-byte transaction Merkle proof forgery vulnerability (documented inline and linked to the BitMEX disclosure). Its design is a two-step gate:

1. Verify the coinbase transaction's Merkle proof against the block's `merkle_root` (the new guard).
2. Delegate to `verify_transaction_inclusion` (v1) for the ordinary tx-inclusion check. [1](#0-0) 

The deprecated v1 function is still a `pub` method decorated only with `#[pause]` — no role restriction, no removal. Any unprivileged NEAR account can call it directly: [2](#0-1) 

The v1 function computes the Merkle root from the caller-supplied `tx_id` and `merkle_proof` and compares it to `header.block_header.merkle_root`: [3](#0-2) 

Because there is no coinbase anchor, an attacker can supply an internal Merkle-tree node as `tx_id`. The 64-byte concatenation of two real child hashes is itself a valid pre-image for a parent node, so `compute_root_from_merkle_proof` will reconstruct the correct `merkle_root` even though `tx_id` is not a real transaction. The function returns `true`. [4](#0-3) 

The coinbase check in v2 prevents this because the coinbase transaction is always at index 0 and its proof path shares siblings with every other transaction; a forged internal node cannot simultaneously satisfy both the coinbase path and the target-tx path. By calling v1 directly, the attacker skips that guard entirely.

---

### Impact Explanation

Any recipient contract or off-chain system that calls `verify_transaction_inclusion` (v1) to gate a high-value action (e.g., releasing bridged BTC, minting wrapped tokens, settling a payment) will accept a forged proof as genuine. The corrupted value is the **proof result** (`true` for a non-existent transaction). This enables an attacker to claim an arbitrary Bitcoin transaction was confirmed in a real, on-chain block header without that transaction ever existing, potentially draining bridge funds or minting unbacked assets.

---

### Likelihood Explanation

The entry point is public, requires no special role, and is reachable by any NEAR account. The only prerequisite is knowledge of a real block header already stored in the contract (trivially obtained via `get_block_hash_by_height` / `get_last_block_header`) and the ability to construct a valid internal-node pre-image from that block's Merkle tree — a well-documented, low-cost offline computation. The function is marked deprecated in Rust source but Rust deprecation warnings are compile-time only; the WASM ABI exposes the method identically to any other public function. Likelihood is **high** for any deployment where a downstream contract has not been audited to confirm it exclusively calls v2.

---

### Recommendation

Remove `verify_transaction_inclusion` (v1) from the public ABI entirely, or gate it with an access-control role that prevents unprivileged callers from invoking it. If backward compatibility must be preserved for a migration window, add a runtime `require!` that panics unconditionally with a clear message directing callers to v2, so the method cannot return a result under any circumstances.

---

### Proof of Concept

1. Identify any block hash stored in the mainchain, e.g. via `get_block_hash_by_height(H)` → `block_hash`.
2. Obtain the block's `merkle_root` from `get_last_block_header()` or any off-chain Bitcoin explorer.
3. Select any two adjacent leaf hashes `L` and `R` from that block's transaction list. Their parent node `P = double_sha256(L || R)` is an internal Merkle-tree node.
4. Build a `merkle_proof` path from `P` up to `merkle_root` using the remaining sibling hashes (all publicly available from a Bitcoin node).
5. Call `verify_transaction_inclusion` with `tx_id = P`, `tx_index = <index of P's position in the next level>`, `merkle_proof = <path from step 4>`, `tx_block_blockhash = block_hash`, `confirmations = 1`.
6. The function returns `true` even though `P` is not a real transaction hash, because `compute_root_from_merkle_proof(P, index, proof) == merkle_root` holds by construction. [5](#0-4) [4](#0-3)

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
