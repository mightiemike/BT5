### Title
Deprecated `verify_transaction_inclusion` (v1) Remains Publicly Callable, Enabling 64-Byte Merkle Proof Forgery — (`contract/src/lib.rs`)

### Summary

The contract exposes two proof-verification entry points. The v2 variant (`verify_transaction_inclusion_v2`) was introduced specifically to close the 64-byte transaction Merkle proof forgery vulnerability by requiring a coinbase Merkle proof. However, the original v1 function (`verify_transaction_inclusion`) carries no `#[private]` guard and remains fully reachable by any unprivileged NEAR caller. An attacker can call v1 directly with a crafted 64-byte fake transaction and a forged Merkle path, causing the function to return `true` for a transaction that was never included in any Bitcoin block. Any downstream dApp that gates fund release or cross-chain actions on this return value is exploitable.

### Finding Description

`verify_transaction_inclusion_v2` documents the threat it closes: [1](#0-0) 

The fix is enforced only inside v2, which validates a coinbase Merkle proof before delegating to v1: [2](#0-1) 

v1 itself is still a `pub fn` with no `#[private]` attribute and no role guard. Any NEAR account can invoke it directly via RPC, completely bypassing the coinbase-proof check that v2 adds. The `#[deprecated]` Rust attribute only emits a compiler warning for Rust callers; it has zero effect on on-chain reachability.

The attacker-controlled parameters are `tx_id`, `tx_index`, and `merkle_proof` inside `ProofArgs`. By supplying a 64-byte fake transaction whose double-SHA256 hash collides with an internal Merkle tree node of a real block, an attacker can construct a `merkle_proof` path that causes the Merkle root recomputation to match the stored header's `merkle_root`, making the function return `true` for a transaction that does not exist.

### Impact Explanation

Any dApp or bridge contract that calls `verify_transaction_inclusion` (v1) to confirm a Bitcoin deposit before releasing wrapped tokens or executing a cross-chain action will accept a forged proof. The corrupted value is the boolean proof result returned to the caller — the same class of broken invariant as the lottery result corruption in the reference report. Funds can be drained or unauthorized cross-chain actions triggered with no valid Bitcoin transaction ever having occurred.

### Likelihood Explanation

The 64-byte Merkle forgery attack is a well-documented, practical exploit (BitMEX research, CVE-2017-12842). The entry path requires no privileged role, no leaked key, and no social engineering — any NEAR account can call `verify_transaction_inclusion` directly. The only prerequisite is constructing a 64-byte fake transaction whose hash matches an internal node of a real block's Merkle tree, which is feasible offline.

### Recommendation

Add `#[private]` to `verify_transaction_inclusion` (v1) to restrict it to internal calls only (i.e., only callable by the contract itself, as v2 already does). All external callers must be directed to `verify_transaction_inclusion_v2`. Alternatively, remove the v1 public export entirely and have v2 inline the Merkle check rather than delegating.

### Proof of Concept

1. Deploy the contract and submit a real Bitcoin block header (e.g., block at height H with a known Merkle root R).
2. Offline, construct a 64-byte fake transaction `fake_tx` such that `SHA256d(fake_tx)` equals an internal Merkle node of block H's Merkle tree.
3. Build a `merkle_proof` path from that internal node up to R.
4. Call `verify_transaction_inclusion` directly (not v2) with `tx_id = SHA256d(fake_tx)`, `tx_block_blockhash = hash_of_block_H`, `tx_index = <crafted index>`, `merkle_proof = <crafted path>`, `confirmations = 0`.
5. The function recomputes the Merkle root using only the supplied path and returns `true`, despite `fake_tx` never appearing in any Bitcoin block.
6. A dApp listening for this `true` result releases funds or executes a cross-chain action based on the forged proof. [3](#0-2)

### Citations

**File:** contract/src/lib.rs (L326-329)
```rust
    /// with an additional coinbase merkle proof validation.
    /// This is needed to mitigate the 64-byte transaction Merkle proof forgery vulnerability:
    /// https://www.bitmex.com/blog/64-Byte-Transactions
    ///
```

**File:** contract/src/lib.rs (L346-368)
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
```
