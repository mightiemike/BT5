### Title
Caller-Supplied `confirmations: 0` Bypasses Finality Guarantee in SPV Proof Verification - (File: `contract/src/lib.rs`)

### Summary

The `verify_transaction_inclusion` and `verify_transaction_inclusion_v2` functions accept a caller-supplied `confirmations: u64` field with no minimum value enforced by the contract. Any unprivileged NEAR caller can pass `confirmations: 0`, causing the contract to return `true` for a transaction that sits at the chain tip with zero subsequent blocks confirming it. Recipient contracts consuming this result treat the transaction as finalized when it is trivially reversible by a Bitcoin chain reorganization.

### Finding Description

Both `ProofArgs` and `ProofArgsV2` carry a `confirmations: u64` field that is entirely caller-controlled. [1](#0-0) 

Inside `verify_transaction_inclusion`, the contract enforces only an **upper** bound — that `confirmations` does not exceed `gc_threshold` — and then checks that enough blocks exist above the target block: [2](#0-1) 

When `args.confirmations == 0`, the second `require!` evaluates to `(any_u64_value) >= 0`, which is trivially true for every possible chain state. The contract therefore returns `true` for a transaction whose block is the current chain tip, with no subsequent blocks building on it. `verify_transaction_inclusion_v2` delegates directly to this same path after its coinbase-proof check: [3](#0-2) 

The test suite itself demonstrates and accepts this behavior — every passing test uses `confirmations: 0`: [4](#0-3) 

There is no contract-side floor. The `confirmations` parameter is documented as a security parameter ("how many confirmed blocks we want to have before the transaction is valid"), yet the contract enforces no minimum, leaving the entire finality decision to the caller.

### Impact Explanation

Any bridge, DEX, or cross-chain application that calls `verify_transaction_inclusion_v2` and trusts the returned `true` to release funds or mint tokens can be exploited. An attacker submits a Bitcoin transaction, immediately calls the NEAR contract with `confirmations: 0` before any reorg window closes, receives `true`, and triggers the downstream action. If the Bitcoin transaction is then reorganized away, the attacker has obtained the cross-chain asset for free. The corrupted invariant is the **proof result**: the contract returns `true` for a transaction that has not achieved any finality.

### Likelihood Explanation

The entry path requires no privilege: `verify_transaction_inclusion_v2` is a public view-like call accessible to any NEAR account. The attacker only needs to know a valid transaction in the most recent block and supply a correct Merkle proof — both are publicly available from the Bitcoin mempool and block data. The cost is a single NEAR function call.

### Recommendation

Enforce a protocol-level minimum confirmation count inside the contract, analogous to the recommended `defaultFee` in the Sablier report:

```rust
const MIN_CONFIRMATIONS: u64 = 6; // or a configurable on-chain parameter

pub fn verify_transaction_inclusion(&self, args: ProofArgs) -> bool {
    require!(
        args.confirmations >= MIN_CONFIRMATIONS,
        format!("confirmations must be at least {}", MIN_CONFIRMATIONS)
    );
    require!(
        args.confirmations <= self.gc_threshold,
        "The required number of confirmations exceeds the number of blocks stored in memory"
    );
    // ... rest of logic
}
```

Alternatively, store a `min_confirmations: u64` field in the contract state (set at `init` and updatable by DAO) so the floor can be adjusted per chain without redeployment.

### Proof of Concept

1. Relayer submits block `N` (the current chain tip) containing attacker's Bitcoin transaction `T`.
2. Attacker immediately calls `verify_transaction_inclusion_v2` from any NEAR account with:
   - `tx_id` = hash of `T`
   - `tx_block_blockhash` = hash of block `N`
   - valid `merkle_proof` for `T` in block `N`
   - `confirmations: 0`
3. The check at line 304–308 evaluates: `(N - N) + 1 = 1 >= 0` → passes.
4. Contract returns `true`.
5. Downstream bridge contract releases funds.
6. Bitcoin miners reorganize block `N`; transaction `T` is gone. Attacker keeps the cross-chain assets. [5](#0-4) [6](#0-5)

### Citations

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

**File:** btc-types/src/contract_args.rs (L28-36)
```rust
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

**File:** contract/src/lib.rs (L289-308)
```rust
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

**File:** contract/src/lib.rs (L367-369)
```rust
        #[allow(deprecated)]
        self.verify_transaction_inclusion(args.into())
    }
```

**File:** contract/tests/test_basics.rs (L796-806)
```rust
            .args_borsh(ProofArgsV2 {
                tx_id: tx_hash.clone(),
                tx_block_blockhash: block.block_hash(),
                tx_index: 1,
                merkle_proof: vec![coinbase_hash.clone()],
                coinbase_tx_id: coinbase_hash,
                coinbase_merkle_proof: vec![tx_hash],
                confirmations: 0,
            })
            .await?
            .json()?;
```
