### Title
Caller-Controlled `confirmations` Parameter with No Minimum Enforced Allows Zero-Confirmation Transaction Acceptance — (File: `contract/src/lib.rs`)

---

### Summary

The `verify_transaction_inclusion` and `verify_transaction_inclusion_v2` functions accept a fully caller-controlled `confirmations` parameter with no lower bound enforced. Any unprivileged NEAR caller can pass `confirmations = 0`, causing the confirmation-depth check to always pass (since any `u64 >= 0` in Rust), bypassing the reorg-protection guarantee the parameter is meant to provide.

---

### Finding Description

Both public verification entry points accept `confirmations` as part of their argument structs with no minimum enforced by the contract.

In `verify_transaction_inclusion`, the only bounds check is an upper bound:

```rust
require!(
    args.confirmations <= self.gc_threshold,
    "The required number of confirmations exceeds the number of blocks stored in memory"
);
``` [1](#0-0) 

The actual depth check is:

```rust
require!(
    (heaviest_block_header.block_height).saturating_sub(target_block_height) + 1
        >= args.confirmations,
    "Not enough blocks confirmed"
);
``` [2](#0-1) 

With `confirmations = 0`, the expression `(height_diff + 1) >= 0` is trivially true for any `u64`, so the check always passes. The function returns `true` for any transaction in the current mainchain regardless of how recently the block was included.

`verify_transaction_inclusion_v2` delegates to `verify_transaction_inclusion` after its coinbase proof check, so it inherits the same flaw:

```rust
#[allow(deprecated)]
self.verify_transaction_inclusion(args.into())
``` [3](#0-2) 

The `confirmations` field is defined in `ProofArgs` and `ProofArgsV2` as a plain `u64` with no validation annotation: [4](#0-3) 

The contract provides no documented minimum safe value and no enforcement of one.

---

### Impact Explanation

The `confirmations` parameter is the sole mechanism by which consumer contracts (bridges, atomic swaps, cross-chain lending protocols) obtain a finality guarantee before acting on a Bitcoin transaction. With `confirmations = 0`, the contract returns `true` for a transaction included in a block that was just added to the mainchain tip, with zero depth. The corrupted value is the **proof result** returned to the consumer contract. A consumer that acts on this result is exposed to a Bitcoin chain reorganization: the attacker submits a transaction, triggers the consumer contract before sufficient depth is reached, and then reorganizes the Bitcoin chain to reverse the transaction. The system is explicitly designed as a foundational layer for bridges and cross-chain protocols, making this a high-value target. [5](#0-4) 

---

### Likelihood Explanation

Medium. `verify_transaction_inclusion` and `verify_transaction_inclusion_v2` are public, unpermissioned NEAR functions (only `#[pause]`, no role restriction). Any NEAR caller — including consumer contracts that do not carefully validate their own inputs — can invoke them with `confirmations = 0`. The contract provides no guidance on a minimum safe value, and the zero-confirmation path is silently accepted rather than rejected. Consumer contracts that omit or misconfigure this parameter receive a false security guarantee.

---

### Recommendation

Enforce a protocol-level minimum confirmation count (e.g., `require!(args.confirmations >= MIN_CONFIRMATIONS, ...)`) inside `verify_transaction_inclusion`. Document the minimum safe confirmation count for each supported chain and threat model, including the attacker advantage from controlling Bitcoin mining hashrate relative to the chosen confirmation window — directly analogous to the VDF difficulty parameter guidance gap identified in the external report.

---

### Proof of Concept

1. Deploy the BTC light client with `skip_pow_verification = false` and a valid genesis.
2. Submit one Bitcoin block header containing a target transaction via `submit_blocks`.
3. Call `verify_transaction_inclusion` with `tx_block_blockhash` set to the just-submitted block and `confirmations = 0`.
4. The check `(tip_height - target_height + 1) >= 0` evaluates to `1 >= 0` → `true`; the function returns `true` immediately.
5. A consumer bridge contract that acts on this result has accepted a zero-confirmation deposit, vulnerable to a reorg reversal. [6](#0-5)

### Citations

**File:** contract/src/lib.rs (L263-288)
```rust
    /// Verifies that a transaction is included in a block at a given block height
    ///
    /// # Deprecated
    /// Use [`verify_transaction_inclusion_v2`] instead, which includes coinbase merkle proof validation
    /// to mitigate the 64-byte transaction Merkle proof forgery vulnerability:
    /// https://www.bitmex.com/blog/64-Byte-Transactions
    ///
    /// @param `tx_id` transaction identifier
    /// @param `tx_block_blockhash` block hash at which transacton is supposedly included
    /// @param `tx_index` index of transaction in the block's tx merkle tree
    /// @param `merkle_proof` merkle tree path (concatenated LE sha256 hashes) (does not contain initial `transaction_hash` and `merkle_root`)
    /// @param confirmations how many confirmed blocks we want to have before the transaction is valid
    /// @return True if `tx_id` is at the claimed position in the block at the given blockhash, False otherwise
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

**File:** contract/src/lib.rs (L289-292)
```rust
        require!(
            args.confirmations <= self.gc_threshold,
            "The required number of confirmations exceeds the number of blocks stored in memory"
        );
```

**File:** contract/src/lib.rs (L304-323)
```rust
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

**File:** contract/src/lib.rs (L367-368)
```rust
        #[allow(deprecated)]
        self.verify_transaction_inclusion(args.into())
```

**File:** btc-types/src/contract_args.rs (L16-25)
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
