### Title
`verify_transaction_inclusion` Bypasses 64-Byte Merkle Forgery Protection Enforced in `verify_transaction_inclusion_v2` — (File: `contract/src/lib.rs`)

---

### Summary

The deprecated `verify_transaction_inclusion` (v1) function remains a publicly callable, unrestricted on-chain entry point that omits the coinbase Merkle proof check that `verify_transaction_inclusion_v2` enforces. An unprivileged NEAR caller can invoke v1 directly with a crafted internal-node hash as `tx_id`, obtaining a `true` proof result without the 64-byte Merkle forgery protection, corrupting the exact security invariant the light client is supposed to guarantee.

---

### Finding Description

`verify_transaction_inclusion_v2` was introduced specifically to mitigate the 64-byte transaction Merkle proof forgery vulnerability. It anchors the Merkle tree structure by requiring a coinbase proof at index 0: [1](#0-0) 

However, `verify_transaction_inclusion` (v1) is still a `pub` function decorated only with `#[pause]` and `#[deprecated]`. The `#[deprecated]` attribute is a compile-time warning only — it imposes **no runtime restriction**. Any unprivileged NEAR account can call v1 directly at any time the contract is not paused. [2](#0-1) 

v1 only verifies:

```
compute_root_from_merkle_proof(tx_id, tx_index, merkle_proof) == header.block_header.merkle_root
```

with no coinbase proof check. The function's own documentation acknowledges this:

> **Warning**: This function may return `true` if the provided `tx_id` is a hash of an internal node in the Merkle tree rather than a valid transaction hash. [3](#0-2) 

An attacker who knows a mainchain block's Merkle tree (all Bitcoin data is public) can craft a `tx_id` equal to an internal 64-byte node hash and a `merkle_proof` that makes the computation return the correct `merkle_root`, causing v1 to return `true` for a transaction that never existed.

The asymmetry is exact: v2 checks both the coinbase proof **and** the transaction proof; v1 checks only the transaction proof — the same incomplete-check pattern as the external report's `safeBatchTransferFrom` missing `NoBlacklist(from)`. [4](#0-3) 

---

### Impact Explanation

Any recipient NEAR contract consuming the `true` result from `verify_transaction_inclusion` can be deceived into accepting a forged Bitcoin transaction inclusion proof. This directly corrupts the **proof result** — the core security output of the light client — and enables false confirmation of Bitcoin transactions that never occurred. Any protocol (bridge, escrow, settlement layer) built on top of this light client and using v1 is exploitable.

**Impact: Medium**

---

### Likelihood Explanation

The function is callable by any unprivileged NEAR account with no staking, role, or deposit requirement beyond the contract not being paused. The attack requires only public Bitcoin blockchain data (the Merkle tree of any mainchain block). The `#[deprecated]` marker does not prevent on-chain invocation. Any consumer contract that calls v1 directly — which is a realistic scenario given v1 is still exported — is vulnerable.

**Likelihood: Medium**

---

### Recommendation

Remove the public accessibility of `verify_transaction_inclusion` or add a runtime guard that prevents direct external calls:

```rust
// Option A: make it private
fn verify_transaction_inclusion(&self, args: ProofArgs) -> bool { ... }

// Option B: add a runtime panic for external callers
pub fn verify_transaction_inclusion(&self, args: ProofArgs) -> bool {
    env::panic_str("Deprecated: use verify_transaction_inclusion_v2");
}
```

Alternatively, add the same coinbase proof check to v1 so both entry points enforce the same invariant.

---

### Proof of Concept

1. Attacker identifies a mainchain block `B` with a known Merkle tree (all public Bitcoin data).
2. Attacker selects an internal Merkle node `N` (a 64-byte concatenation of two child hashes) and sets `tx_id = hash(N)`.
3. Attacker constructs `merkle_proof` such that `compute_root_from_merkle_proof(tx_id, tx_index, merkle_proof) == B.merkle_root`.
4. Attacker calls `verify_transaction_inclusion` with `tx_block_blockhash = B.hash`, `tx_id = crafted_id`, `merkle_proof = crafted_proof`, `confirmations = 1`.
5. v1 passes all checks — block is in mainchain, confirmations satisfied, merkle root matches — and returns `true`.
6. Any recipient NEAR contract consuming this result accepts the forged proof, falsely confirming a Bitcoin transaction that never existed. [5](#0-4)

### Citations

**File:** contract/src/lib.rs (L276-281)
```rust
    ///
    /// # Warning
    /// This function may return `true` if the provided `tx_id` is a hash of an internal node in the Merkle tree rather than a valid transaction hash.
    /// We assume that validation of whether the `tx_id` corresponds to a valid transaction hash is performed at a higher level of verification.
    ///
    /// # Panics
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
