### Title
Deprecated `verify_transaction_inclusion` Remains Callable, Allowing Proof Reuse to Bypass Coinbase Merkle Validation — (File: `contract/src/lib.rs`)

---

### Summary

The deprecated `verify_transaction_inclusion` (v1) function is still publicly callable by any unprivileged NEAR account. Because `ProofArgsV2` structurally contains `ProofArgs` as a strict subset, any proof submitted to `verify_transaction_inclusion_v2` (v2) can be trivially re-submitted to v1 by dropping the coinbase fields. This bypasses the coinbase Merkle proof validation that v2 was specifically introduced to enforce, and allows the 64-byte transaction Merkle proof forgery attack to produce a corrupted `true` proof result.

---

### Finding Description

The contract exposes two proof-verification entry points:

- `verify_transaction_inclusion` (v1, `#[deprecated]`): validates only that `compute_root_from_merkle_proof(tx_id, tx_index, merkle_proof) == header.merkle_root`.
- `verify_transaction_inclusion_v2` (v2): additionally requires a coinbase Merkle proof at index 0 to match the same Merkle root before delegating to v1 internally. [1](#0-0) 

The `From<ProofArgsV2> for ProofArgs` conversion drops `coinbase_tx_id` and `coinbase_merkle_proof` entirely: [2](#0-1) 

This means the `ProofArgs`-shaped fields inside any `ProofArgsV2` call can be extracted and submitted verbatim to v1. More critically, an attacker does not even need to observe a prior v2 call — they can craft a standalone `ProofArgs` payload and call v1 directly, since v1 is still a live, `#[pause]`-gated (but not access-controlled) public method.

The 64-byte transaction Merkle proof forgery works as follows: Bitcoin's Merkle tree construction allows an internal node (32 + 32 bytes = 64 bytes) to be interpreted as a leaf transaction hash. An attacker can supply a `tx_id` that is actually an internal node hash and a crafted `merkle_proof` path such that `compute_root_from_merkle_proof` returns the real block's `merkle_root`. v1 accepts this and returns `true`. v2 would reject it because the coinbase proof at index 0 would not match. [3](#0-2) 

The contract's own deprecation notice acknowledges the forgery risk and directs callers to v2, but does not remove or restrict v1: [4](#0-3) 

---

### Impact Explanation

Any downstream NEAR contract that calls `verify_transaction_inclusion` (v1) receives a corrupted proof result — `true` — for a transaction that does not exist in the claimed block. This directly corrupts the **proof result** output of the light client, which is the primary security guarantee the contract provides to consumers (e.g., bridge contracts releasing funds, cross-chain applications gating state transitions on BTC transaction inclusion). An attacker can fabricate a false SPV proof and have it accepted on-chain.

---

### Likelihood Explanation

The entry point is fully permissionless — any NEAR account can call `verify_transaction_inclusion` with attacker-controlled `ProofArgs`. No staking, role, or privileged key is required. The 64-byte Merkle forgery technique is publicly documented (referenced in the contract's own comments at line 269) and has known tooling. The only prerequisite is knowledge of a real block's Merkle root, which is public on-chain state. [5](#0-4) 

---

### Recommendation

Remove `verify_transaction_inclusion` (v1) entirely from the contract, or gate it with an access-control role so it cannot be called by external accounts. The `#[deprecated]` attribute has no on-chain enforcement — it is a Rust compiler hint only and does not prevent NEAR callers from invoking the method. All external callers should be directed exclusively to `verify_transaction_inclusion_v2`.

---

### Proof of Concept

1. Attacker identifies a real Bitcoin block stored in the contract with known `merkle_root` (readable via `get_last_block_header` or `get_block_hash_by_height`).
2. Attacker selects an internal Merkle tree node hash from that block as the forged `tx_id`.
3. Attacker constructs a `ProofArgs` with the forged `tx_id`, the real `tx_block_blockhash`, a crafted `merkle_proof` path, and any `tx_index` such that `compute_root_from_merkle_proof(forged_tx_id, tx_index, merkle_proof) == real_merkle_root`.
4. Attacker calls `verify_transaction_inclusion` directly on the NEAR contract with these args.
5. The function returns `true` — a corrupted proof result — for a transaction that does not exist.
6. A downstream bridge or application contract that gates fund release on this `true` result is deceived into executing an unauthorized action. [6](#0-5) [7](#0-6)

### Citations

**File:** contract/src/lib.rs (L263-323)
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

**File:** btc-types/src/contract_args.rs (L38-47)
```rust
impl From<ProofArgsV2> for ProofArgs {
    fn from(args: ProofArgsV2) -> Self {
        Self {
            tx_id: args.tx_id,
            tx_block_blockhash: args.tx_block_blockhash,
            tx_index: args.tx_index,
            merkle_proof: args.merkle_proof,
            confirmations: args.confirmations,
        }
    }
```
