### Title
Chain Reorganization Completes Without Emitting a Structured Event - (File: `contract/src/lib.rs`)

### Summary
When a fork accumulates more chainwork than the current mainchain and `reorg_chain` executes, the contract silently rewrites the canonical chain tip and demotes previously mainchain blocks using only an unstructured plain-text `log!("Chain reorg")` call. No NEP-297-compliant structured event is emitted. Off-chain clients — including the relayer `Synchronizer` and any downstream NEAR contract consuming SPV proofs — have no reliable, machine-parseable signal that the canonical chain has changed, which blocks they were demoted, or what the new tip is.

### Finding Description
`submit_block_header_inner` detects a fork overtaking the mainchain and calls `reorg_chain`: [1](#0-0) 

`reorg_chain` then walks the fork back to the common ancestor, promotes every fork block into `mainchain_height_to_header` / `mainchain_header_to_height`, removes the displaced mainchain blocks from `headers_pool`, and finally updates `mainchain_tip_blockhash`: [2](#0-1) 

The entire reorg — which can silently invalidate an arbitrary number of previously mainchain blocks — produces no structured event. The only output is the plain-text `log!("Chain reorg")` emitted just before the call: [3](#0-2) 

In NEAR Protocol, structured events must follow the NEP-297 standard (`EVENT_JSON:{...}` prefix via `env::log_str`). A bare `log!()` macro call is unindexed debug text; NEAR indexers and off-chain listeners cannot reliably distinguish it from any other log line, cannot extract typed fields from it, and cannot subscribe to it as a contract event. The contract imports `log` from `near_sdk` but never defines or emits any NEP-297 event type anywhere in the codebase: [4](#0-3) 

### Impact Explanation
Any NEAR contract or off-chain service that previously received a `true` result from `verify_transaction_inclusion` or `verify_transaction_inclusion_v2` for a block that is subsequently demoted by a reorg has no on-chain signal to invalidate that cached result. The demoted block is removed from `mainchain_header_to_height`: [5](#0-4) 

A downstream contract that stored the `true` SPV result and later acts on it (e.g., releasing funds, minting tokens) will act on a proof that is no longer valid against the canonical chain. The contract itself would now correctly reject a fresh call for the same block (it is no longer in `mainchain_header_to_height`), but there is no event to trigger re-validation of previously accepted proofs. The relayer `Synchronizer` also has no structured signal to detect that its view of the canonical tip has changed beneath it.

### Likelihood Explanation
Chain reorganizations are a normal, expected part of Bitcoin operation. Any unprivileged relayer can trigger this path by submitting a sequence of valid fork headers whose cumulative chainwork exceeds the current mainchain tip — exactly the intended protocol behavior. The entry point is the public, payable `submit_blocks` function: [6](#0-5) 

No privileged role is required. The reorg path is exercised by the existing integration test suite, confirming it is reachable in production: [7](#0-6) 

### Recommendation
Define a NEP-297-compliant event struct (e.g., `ChainReorgEvent`) carrying at minimum the old tip hash, new tip hash, common ancestor hash, and reorg depth. Emit it via `env::log_str` with the `EVENT_JSON:` prefix at the end of `reorg_chain`, after `mainchain_tip_blockhash` is updated. Similarly, emit a structured `BlockSubmitted` event from `store_block_header` and a `GarbageCollected` event from `run_mainchain_gc` so that off-chain indexers can maintain a consistent view of contract state without polling.

### Proof of Concept
1. Deploy the contract with `skip_pow_verification = true`.
2. Submit a mainchain block `M1` extending the genesis tip → `mainchain_tip_blockhash = hash(M1)`.
3. Submit fork block `F1` (same parent as `M1`, higher `bits`/work) → stored as fork.
4. Submit fork block `F2` extending `F1` with enough cumulative work to exceed `M1` → `reorg_chain` fires, `M1` is removed from `headers_pool`, `mainchain_tip_blockhash = hash(F2)`.
5. Inspect the transaction receipt logs: only the string `"Block ...: saving to fork"` and `"Chain reorg"` appear — no `EVENT_JSON:` line is present.
6. A downstream contract that called `verify_transaction_inclusion` for a tx in `M1` before step 4 received `true`; after step 4 the same call panics with `"block does not belong to the current main chain"`, but no on-chain event was emitted to prompt re-validation of the earlier result. [8](#0-7)

### Citations

**File:** contract/src/lib.rs (L15-15)
```rust
use near_sdk::{env, log, near, require, NearToken, PanicOnDefault, Promise, PromiseOrValue};
```

**File:** contract/src/lib.rs (L169-172)
```rust
    pub fn submit_blocks(
        &mut self,
        #[serializer(borsh)] headers: Vec<BlockHeader>,
    ) -> PromiseOrValue<()> {
```

**File:** contract/src/lib.rs (L531-568)
```rust
    fn submit_block_header_inner(
        &mut self,
        current_header: ExtendedHeader,
        prev_block_header: &ExtendedHeader,
    ) {
        // Main chain submission
        if prev_block_header.block_hash == self.mainchain_tip_blockhash {
            // Probably we should check if it is not in a mainchain?
            // chainwork > highScore
            log!("Block {}: saving to mainchain", current_header.block_hash);
            // Validate chain
            assert_eq!(
                self.mainchain_tip_blockhash,
                current_header.block_header.prev_block_hash
            );

            self.store_block_header(&current_header);
            self.mainchain_tip_blockhash = current_header.block_hash;
        } else {
            log!("Block {}: saving to fork", current_header.block_hash);
            // Fork submission
            let main_chain_tip_header = self
                .headers_pool
                .get(&self.mainchain_tip_blockhash)
                .unwrap_or_else(|| env::panic_str("tip should be in a header pool"));

            let last_main_chain_block_height = main_chain_tip_header.block_height;
            let total_main_chain_chainwork = main_chain_tip_header.chain_work;

            self.store_fork_header(&current_header);

            // Current chainwork is higher than on a current mainchain, let's promote the fork
            if current_header.chain_work > total_main_chain_chainwork {
                log!("Chain reorg");
                self.reorg_chain(current_header, last_main_chain_block_height);
            }
        }
    }
```

**File:** contract/src/lib.rs (L645-647)
```rust
        // Updating tip of the new main chain
        self.mainchain_tip_blockhash = fork_tip_hash;
    }
```

**File:** contract/src/lib.rs (L658-661)
```rust
    /// Remove block header and meta information
    fn remove_block_header(&mut self, header_block_hash: &H256) {
        self.mainchain_header_to_height.remove(header_block_hash);
        self.headers_pool.remove(header_block_hash);
```

**File:** contract/tests/test_basics.rs (L312-348)
```rust
    #[tokio::test]
    async fn test_setting_chain_reorg() -> Result<(), Box<dyn std::error::Error>> {
        let (contract, user_account) = init_contract().await?;
        let (main_block, fork_1, fork_2) = make_reorg_test_blocks();

        let storage_usage_init = contract.view_account().await.unwrap().storage_usage;

        // main_block extends fake_0 (current tip) → goes to mainchain at height 2
        let outcome = user_account
            .call(contract.id(), "submit_blocks")
            .args_borsh([main_block].to_vec())
            .deposit(STORAGE_DEPOSIT_PER_BLOCK)
            .transact()
            .await?;
        assert!(outcome.is_success());

        let storage_usage_one_block = contract.view_account().await.unwrap().storage_usage;

        // fork_1 also extends fake_0 but as a fork (same chainwork → not promoted)
        let outcome = user_account
            .call(contract.id(), "submit_blocks")
            .args_borsh([fork_1].to_vec())
            .deposit(STORAGE_DEPOSIT_PER_BLOCK)
            .transact()
            .await?;
        assert!(outcome.is_success());

        let storage_usage_fork = contract.view_account().await.unwrap().storage_usage;

        // fork_2 extends fork_1 (higher chainwork → reorg, becomes new tip)
        let outcome = user_account
            .call(contract.id(), "submit_blocks")
            .args_borsh([fork_2.clone()].to_vec())
            .deposit(STORAGE_DEPOSIT_PER_BLOCK)
            .transact()
            .await?;
        assert!(outcome.is_success());
```
