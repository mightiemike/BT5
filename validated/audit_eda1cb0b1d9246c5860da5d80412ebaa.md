### Title
Unbounded `reorg_chain` While Loop Can Exhaust NEAR Gas, Permanently Stranding the Contract on a Stale Chain - (`File: contract/src/lib.rs`)

### Summary
The `reorg_chain` function contains an unbounded `while` loop that iterates once per diverging block in a fork, performing multiple storage operations per iteration. If a fork is long enough, the loop exhausts the NEAR transaction gas limit (300 TGas), causing the entire `submit_blocks` call to revert. The contract then permanently fails to promote the heavier fork to the mainchain, violating the core light-client invariant of tracking the chain with the most cumulative work.

### Finding Description
When a submitted block's `chain_work` exceeds the current mainchain tip's `chain_work`, `submit_block_header_inner` calls `reorg_chain`. [1](#0-0) 

Inside `reorg_chain`, two loops run sequentially:

**Loop 1** — removes excess mainchain blocks when the mainchain is taller than the fork: [2](#0-1) 

**Loop 2** — walks the fork backwards from its tip until it reaches the common ancestor, promoting each fork block into the mainchain and evicting the displaced mainchain block: [3](#0-2) 

Each iteration of Loop 2 performs at minimum:
- `mainchain_header_to_height.contains_key` — storage read
- `mainchain_height_to_header.insert` — storage write
- `mainchain_header_to_height.insert` — storage write
- `remove_block_header` (when a mainchain block is displaced) — two storage deletes
- `headers_pool.get` — storage read

NEAR storage writes cost ~100 Ggas each; storage deletes are similarly priced. With five storage operations per iteration, each loop pass consumes roughly 400–500 Ggas. The NEAR transaction gas cap is 300 TGas = 300,000 Ggas, giving a practical ceiling of **~600–750 iterations** before OOG. For Dogecoin (faster blocks, documented 51%-attack history) or Litecoin, a fork of that depth is operationally plausible. Even for Bitcoin, the design provides no upper bound and no batching mechanism.

The entry path is the `submit_blocks` public method, callable by any account holding the trusted-relayer role, which is the normal production path for the off-chain relayer service submitting adversarial (long-fork) chain data: [4](#0-3) 

When the transaction reverts due to OOG, all state changes are rolled back. The fork-tip block that triggered the reorg is not persisted in `headers_pool`, so the relayer cannot retry the reorg by re-submitting only the tip — it must re-submit the entire fork from scratch. If the fork is long enough to always exhaust gas, the reorg can never be completed.

### Impact Explanation
The corrupted state is `mainchain_tip_blockhash` and the `mainchain_height_to_header` / `mainchain_header_to_height` mappings: they permanently reflect a stale chain with less cumulative work than the true Bitcoin/Dogecoin/Litecoin tip. Every subsequent call to `verify_transaction_inclusion` or `verify_transaction_inclusion_v2` will validate proofs against the wrong chain, allowing inclusion proofs for transactions that are not on the canonical chain to return `true`, and rejecting proofs for transactions that are on the canonical chain. [5](#0-4) 

### Likelihood Explanation
For Dogecoin (a supported production target per the `Makefile` feature flags), 51%-attack-length forks have occurred historically. The Dogecoin build uses the same `reorg_chain` code path. A relayer faithfully following the Dogecoin network would submit the attacking fork blocks, triggering the unbounded loop. For Bitcoin mainnet the likelihood is low, but the code provides no protection for any chain variant. [6](#0-5) 

### Recommendation
Replace the unbounded while loop in `reorg_chain` with a batched approach: accept a `max_steps` parameter (or derive it from the remaining gas via `env::prepaid_gas() - env::used_gas()`), persist a "reorg-in-progress" cursor in contract state, and allow the reorg to be completed across multiple transactions. Alternatively, enforce a hard cap on the fork depth accepted by `submit_blocks` (e.g., reject any fork whose divergence point is more than N blocks behind the current tip).

### Proof of Concept
1. Deploy the contract (Dogecoin build) with `gc_threshold = 52704`.
2. Have the relayer submit the canonical chain up to height H (mainchain tip = H).
3. Construct a fork starting at height H−700 with higher cumulative work than the mainchain (achievable on Dogecoin testnet with min-difficulty blocks or on a private chain).
4. Submit the 700 fork blocks one by one via `submit_blocks`. Each is stored as a fork header in `headers_pool`.
5. Submit the 701st fork block whose `chain_work` exceeds the mainchain tip's `chain_work`. This triggers `reorg_chain` with a 700-block divergence.
6. The transaction runs OOG inside the `while` loop at approximately iteration 600–750. The transaction reverts.
7. `mainchain_tip_blockhash` remains at height H (the stale chain). The fork is never promoted. `verify_transaction_inclusion` now operates against the wrong chain. [3](#0-2)

### Citations

**File:** contract/src/lib.rs (L169-198)
```rust
    pub fn submit_blocks(
        &mut self,
        #[serializer(borsh)] headers: Vec<BlockHeader>,
    ) -> PromiseOrValue<()> {
        let amount = env::attached_deposit();
        let initial_storage = env::storage_usage();
        let num_of_headers = headers.len().try_into().unwrap();

        for header in headers {
            self.submit_block_header(header, self.skip_pow_verification);
        }

        self.run_mainchain_gc(num_of_headers);
        let diff_storage_usage = env::storage_usage().saturating_sub(initial_storage);
        let required_deposit = env::storage_byte_cost().saturating_mul(diff_storage_usage.into());

        require!(
            amount >= required_deposit,
            format!("Required deposit {}", required_deposit)
        );

        let refund = amount.saturating_sub(required_deposit);
        if refund > NearToken::from_near(0) {
            Promise::new(env::predecessor_account_id())
                .transfer(refund)
                .into()
        } else {
            PromiseOrValue::Value(())
        }
    }
```

**File:** contract/src/lib.rs (L288-323)
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

**File:** contract/src/lib.rs (L562-566)
```rust
            // Current chainwork is higher than on a current mainchain, let's promote the fork
            if current_header.chain_work > total_main_chain_chainwork {
                log!("Chain reorg");
                self.reorg_chain(current_header, last_main_chain_block_height);
            }
```

**File:** contract/src/lib.rs (L586-593)
```rust
            for height in (fork_tip_height + 1)..=last_main_chain_block_height {
                let current_main_chain_blockhash = self
                    .mainchain_height_to_header
                    .get(&height)
                    .unwrap_or_else(|| env::panic_str("cannot get a block"));
                self.remove_block_header(&current_main_chain_blockhash);
                self.mainchain_height_to_header.remove(&height);
            }
```

**File:** contract/src/lib.rs (L616-643)
```rust
        while !self
            .mainchain_header_to_height
            .contains_key(&fork_header_cursor.block_hash)
        {
            let prev_block_hash = fork_header_cursor.block_header.prev_block_hash;
            let current_block_hash = fork_header_cursor.block_hash;
            let current_height = fork_header_cursor.block_height;

            // Inserting the fork block into the main chain, if some mainchain block is occupying
            // this height let's save its hashcode
            let main_chain_block = self
                .mainchain_height_to_header
                .insert(&current_height, &current_block_hash);
            self.mainchain_header_to_height
                .insert(&current_block_hash, &current_height);

            // If we found a mainchain block at the current height than remove this block from the
            // header pool and from the header -> height map
            if let Some(current_main_chain_blockhash) = main_chain_block {
                self.remove_block_header(&current_main_chain_blockhash);
            }

            // Switch iterator cursor to the previous block in fork
            fork_header_cursor = self
                .headers_pool
                .get(&prev_block_hash)
                .unwrap_or_else(|| env::panic_str("previous fork block should be there"));
        }
```

**File:** Makefile (L5-5)
```text
FEATURES = bitcoin dogecoin litecoin zcash
```
