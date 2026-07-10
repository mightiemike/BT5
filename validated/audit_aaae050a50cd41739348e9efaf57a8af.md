### Title
Empty Merkle Proof Causes Unconditional Revert for Single-Transaction Blocks — (`contract/src/lib.rs`)

### Summary

`verify_transaction_inclusion` unconditionally rejects an empty `merkle_proof` via a hard `require!` at line 315. In Bitcoin protocol, a block containing exactly one transaction (the coinbase) has a merkle root equal to that transaction's hash, so the merkle proof for that transaction is legitimately empty. Any unprivileged NEAR caller attempting to verify such a transaction will always receive a revert, making it impossible to use the light client for this valid protocol case. The same defect is inherited by `verify_transaction_inclusion_v2`, which delegates to the deprecated function after its own checks pass.

### Finding Description

In `verify_transaction_inclusion` (`contract/src/lib.rs`, line 315):

```rust
require!(!args.merkle_proof.is_empty(), "Merkle proof is empty");
```

This check is unconditional. It fires before the actual merkle root computation at lines 318–322:

```rust
merkle_tools::compute_root_from_merkle_proof(
    args.tx_id,
    usize::try_from(args.tx_index).unwrap(),
    &args.merkle_proof,
) == header.block_header.merkle_root
```

For a single-transaction block, `compute_root_from_merkle_proof` with an empty proof would return `tx_id` directly, which equals `merkle_root` — the correct result. The guard at line 315 prevents this correct path from ever executing.

`verify_transaction_inclusion_v2` (lines 347–369) does not duplicate this guard but calls `self.verify_transaction_inclusion(args.into())` at line 368, inheriting the revert. Its own pre-checks (length equality at line 349, coinbase proof at lines 358–365) both pass when both proofs are empty and the block is a single-transaction block, so the only failure point is the inherited guard. [1](#0-0) [2](#0-1) [3](#0-2) 

### Impact Explanation

Any downstream NEAR contract or off-chain caller that relies on `verify_transaction_inclusion` or `verify_transaction_inclusion_v2` to confirm a Bitcoin transaction in a single-transaction block will always receive a revert. The light client cannot fulfill its core SPV verification role for this valid protocol input. Protocols that gate asset releases or cross-chain actions on a `true` result from these functions are permanently blocked for this class of blocks. [4](#0-3) [5](#0-4) 

### Likelihood Explanation

Single-transaction blocks (coinbase only) occur on Bitcoin mainnet during periods of very low mempool activity and are a normal, valid protocol state. The entry point (`verify_transaction_inclusion` / `verify_transaction_inclusion_v2`) is a public, unprivileged view call reachable by any NEAR account. No special role, staking, or privileged access is required to trigger the revert. [6](#0-5) [7](#0-6) 

### Recommendation

Guard the empty-proof check so it only fires when the proof is non-empty, mirroring the analog fix recommended in the reference report:

```rust
// Only reject empty proof when the block has more than one transaction.
// An empty proof is valid when tx_id == merkle_root (single-tx block).
if !args.merkle_proof.is_empty() {
    // existing compute_root_from_merkle_proof comparison
} else {
    return args.tx_id == header.block_header.merkle_root;
}
```

Alternatively, remove the `require!(!args.merkle_proof.is_empty())` guard entirely and let `compute_root_from_merkle_proof` handle the empty case, provided that function returns `tx_id` unchanged for an empty proof slice. [8](#0-7) 

### Proof of Concept

1. Deploy the contract (Bitcoin feature flag) with any valid genesis.
2. Submit a block whose merkle root equals a single transaction hash `T` (i.e., the block contains only the coinbase transaction).
3. Call `verify_transaction_inclusion` with:
   - `tx_id = T`
   - `tx_block_blockhash = <hash of that block>`
   - `tx_index = 0`
   - `merkle_proof = []` (empty — correct for a single-tx block)
   - `confirmations = 1`
4. Observe the call reverts with `"Merkle proof is empty"` despite the proof being protocol-correct.
5. Repeat via `verify_transaction_inclusion_v2` with `coinbase_tx_id = T`, `coinbase_merkle_proof = []`, `merkle_proof = []`; the length check (0 == 0) and coinbase check (T == merkle_root) both pass, but the delegated call still reverts at line 315. [1](#0-0) [9](#0-8)

### Citations

**File:** contract/src/lib.rs (L287-323)
```rust
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
