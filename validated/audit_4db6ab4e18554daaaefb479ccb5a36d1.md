### Title
No Minimum Confirmation Enforcement in `verify_transaction_inclusion` Allows Zero-Confirmation SPV Proof Acceptance — (`contract/src/lib.rs`, `btc-types/src/contract_args.rs`)

### Summary
The `verify_transaction_inclusion` and `verify_transaction_inclusion_v2` contract methods accept a caller-supplied `confirmations: u64` with no minimum value enforced. Any unprivileged NEAR caller can pass `confirmations = 0`, causing the contract to return `true` for a transaction in a block with zero depth (the current chain tip), which is trivially reversible by a re-org.

### Finding Description
`ProofArgs` and `ProofArgsV2` define `confirmations` as a plain `u64` with no lower-bound constraint: [1](#0-0) [2](#0-1) 

Inside `verify_transaction_inclusion`, the only guard on `confirmations` is an **upper** bound against `gc_threshold`: [3](#0-2) 

The actual depth check is: [4](#0-3) 

When `args.confirmations = 0`, the expression `depth + 1 >= 0` is always `true` for any `u64`, so the depth check is trivially bypassed. The function proceeds to verify the Merkle proof and returns `true` for a transaction in a block at the chain tip — a block with a single confirmation that can be re-orged away.

`verify_transaction_inclusion_v2` delegates directly to `verify_transaction_inclusion` after its coinbase proof check, so it inherits the same flaw: [5](#0-4) 

### Impact Explanation
A downstream contract or application that calls either verification method with `confirmations = 0` (or `1`) receives a `true` result for a transaction that has not achieved any meaningful finality. If that result gates a fund release, bridge unlock, or other irreversible on-chain action, a re-org on the tracked PoW chain can invalidate the transaction after the NEAR-side action has already executed, causing loss of funds or protocol desynchronization.

### Likelihood Explanation
The entry point is fully permissionless — any NEAR account can call `verify_transaction_inclusion` or `verify_transaction_inclusion_v2` with `confirmations = 0`. No privileged role, leaked key, or social engineering is required. Recipient contracts that delegate the confirmation threshold decision to their own callers (a common pattern) propagate the risk directly.

### Recommendation
Enforce a safe minimum confirmation count at the contract level. Add a `require!(args.confirmations >= MIN_CONFIRMATIONS, ...)` guard at the top of `verify_transaction_inclusion`, where `MIN_CONFIRMATIONS` is a chain-appropriate constant (e.g., 6 for Bitcoin mainnet). The same constant should be documented in `ProofArgs` and `ProofArgsV2` so integrators understand the expected floor. The upper-bound guard against `gc_threshold` already exists; a symmetric lower-bound guard closes the gap.

### Proof of Concept
1. Deploy the contract with any valid genesis and `gc_threshold > 0`.
2. Submit one block header beyond genesis so the chain tip is at height 1.
3. Call `verify_transaction_inclusion_v2` with:
   - `tx_block_blockhash` = the hash of the tip block (height 1, depth 0)
   - a valid Merkle proof for a transaction in that block
   - `confirmations = 0`
4. The contract returns `true`.
5. A re-org that replaces the tip block invalidates the transaction, but the NEAR-side action gated on the `true` result has already executed.

The root cause is the absence of a lower-bound check on `args.confirmations` in `verify_transaction_inclusion`: [6](#0-5)

### Citations

**File:** btc-types/src/contract_args.rs (L18-24)
```rust
pub struct ProofArgs {
    pub tx_id: H256,
    pub tx_block_blockhash: H256,
    pub tx_index: u64,
    pub merkle_proof: Vec<H256>,
    pub confirmations: u64,
}
```

**File:** btc-types/src/contract_args.rs (L26-36)
```rust
#[near(serializers = [borsh, json])]
#[derive(Clone, Debug)]
pub struct ProofArgsV2 {
    pub tx_id: H256,
    pub tx_block_blockhash: H256,
    pub tx_index: u64,
    pub merkle_proof: Vec<H256>,
    pub coinbase_tx_id: H256,
    pub coinbase_merkle_proof: Vec<H256>,
    pub confirmations: u64,
}
```

**File:** contract/src/lib.rs (L288-308)
```rust
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
```

**File:** contract/src/lib.rs (L367-368)
```rust
        #[allow(deprecated)]
        self.verify_transaction_inclusion(args.into())
```
