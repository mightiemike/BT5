### Title
Storage Deposit Funds Permanently Locked — No Withdrawal Mechanism - (File: `contract/src/lib.rs`)

### Summary
The `BtcLightClient` contract collects NEAR token deposits from callers of `submit_blocks` to cover on-chain storage costs. When the garbage collector (`run_mainchain_gc`) later frees that storage, the previously paid deposit tokens remain in the contract's balance with no function to retrieve them. The contract has no `withdraw`, `collect`, or fund-recovery function of any kind, making all accumulated storage deposits permanently inaccessible.

### Finding Description
`submit_blocks` is marked `#[payable]` and requires callers to attach NEAR tokens proportional to the storage consumed by the submitted headers: [1](#0-0) 

The function retains exactly `required_deposit = storage_byte_cost × diff_storage_usage` and refunds only the surplus. Those retained tokens cover the NEAR storage-staking requirement for the newly written header entries.

`run_mainchain_gc` is called automatically inside every `submit_blocks` invocation and can also be called directly. It removes old mainchain headers from `headers_pool`, `mainchain_height_to_header`, and `mainchain_header_to_height`: [2](#0-1) 

In NEAR Protocol, freeing storage reduces the contract's *minimum required balance*, but the actual token balance of the contract account does not decrease — the previously deposited tokens simply become "free" (unlocked) balance sitting in the contract account. Because no withdrawal function exists anywhere in the contract, those tokens are permanently stranded.

A search across all contract source files (`contract/src/lib.rs`, `bitcoin.rs`, `dogecoin.rs`, `litecoin.rs`, `zcash.rs`, `utils.rs`) finds zero occurrences of any withdrawal, transfer-to-owner, or fund-recovery function:



The `Role` enum defines `DAO`, `PauseManager`, `RelayerManager`, and upgrade roles, but none of them are wired to any fund-retrieval method: [3](#0-2) 

### Impact Explanation
Every storage deposit paid by a relayer or block submitter that is later freed by GC becomes permanently locked in the contract. Over the operational lifetime of the contract — which is designed to run continuously, GC-ing thousands of blocks per year (`gc_threshold` defaults to 52,704 blocks/year per the inline documentation) — the accumulated locked balance grows monotonically. Neither the DAO, nor any privileged role, nor any unprivileged caller can recover these funds.

### Likelihood Explanation
The GC path is triggered automatically on every `submit_blocks` call once the mainchain size exceeds `gc_threshold`. This is the normal steady-state operation of the contract. Every production deployment will accumulate locked funds from the first GC cycle onward. No special attacker action is required; the loss occurs through ordinary relayer operation.

### Recommendation
Add a privileged withdrawal function gated behind the `Role::DAO` access-control role that transfers the contract's free balance (i.e., `env::account_balance() - env::storage_byte_cost() * env::storage_usage()`) to a designated recipient. This mirrors the fix applied in the referenced GMX `FeeReceiver` report.

### Proof of Concept

1. Deploy the contract with `gc_threshold = 100`.
2. A relayer calls `submit_blocks` with 50 headers, attaching the required deposit (e.g., 0.5 NEAR). The contract retains the full deposit.
3. The relayer continues submitting until the mainchain exceeds 100 blocks. GC fires inside `submit_blocks`, removing the earliest headers and freeing their storage.
4. The contract's minimum required balance drops (storage freed), but its actual NEAR balance does not — the deposit tokens from step 2 remain in the contract.
5. Attempt to call any function to retrieve those tokens: no such function exists. The tokens are permanently locked. [4](#0-3) [5](#0-4)

### Citations

**File:** contract/src/lib.rs (L38-73)
```rust
#[derive(AccessControlRole, Deserialize, Serialize, Copy, Clone)]
#[serde(crate = "near_sdk::serde")]
pub enum Role {
    /// May pause and unpause features.
    PauseManager,
    /// Allows to use contract API even after contract is paused
    UnrestrictedSubmitBlocks,
    // Allows to use `run_mainchain_gc` API on a paused contract
    UnrestrictedRunGC,
    /// May successfully call any of the protected `Upgradable` methods since below it is passed to
    /// every attribute of `access_control_roles`.
    ///
    /// Using this pattern grantees of a single role are authorized to call all `Upgradable`methods.
    DAO,
    /// May successfully call `Upgradable::up_stage_code`, but none of the other protected methods,
    /// since below is passed only to the `code_stagers` attribute.
    ///
    /// Using this pattern grantees of a role are authorized to call only one particular protected
    /// `Upgradable` method.
    CodeStager,
    /// May successfully call `Upgradable::up_deploy_code`, but none of the other protected methods,
    /// since below is passed only to the `code_deployers` attribute.
    ///
    /// Using this pattern grantees of a role are authorized to call only one particular protected
    /// `Upgradable` method.
    CodeDeployer,
    /// May successfully call `Upgradable` methods to initialize and update the staging duration
    /// since below it is passed to the attributes `duration_initializers`,
    /// `duration_update_stagers`, and `duration_update_appliers`.
    ///
    /// Using this pattern grantees of a single role are authorized to call multiple (but not all)
    /// protected `Upgradable` methods.
    DurationManager,
    /// May manage trusted relayer staking: reject applications and update relayer config.
    RelayerManager,
}
```

**File:** contract/src/lib.rs (L166-198)
```rust
    #[payable]
    #[pause]
    #[trusted_relayer]
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

**File:** contract/src/lib.rs (L377-416)
```rust
    pub fn run_mainchain_gc(&mut self, batch_size: u64) {
        let initial_blockheader = self
            .headers_pool
            .get(&self.mainchain_initial_blockhash)
            .unwrap_or_else(|| env::panic_str(ERR_KEY_NOT_EXIST));

        let tip_blockheader = self
            .headers_pool
            .get(&self.mainchain_tip_blockhash)
            .unwrap_or_else(|| env::panic_str(ERR_KEY_NOT_EXIST));

        let amount_of_headers_we_store =
            tip_blockheader.block_height - initial_blockheader.block_height + 1;

        if amount_of_headers_we_store > self.gc_threshold {
            let total_amount_to_remove = amount_of_headers_we_store - self.gc_threshold;
            let selected_amount_to_remove = std::cmp::min(total_amount_to_remove, batch_size);

            let start_removal_height = initial_blockheader.block_height;
            let end_removal_height = initial_blockheader.block_height + selected_amount_to_remove;
            env::log_str(&format!(
                "Num of blocks to remove {selected_amount_to_remove}"
            ));

            for height in start_removal_height..end_removal_height {
                let blockhash = &self
                    .mainchain_height_to_header
                    .get(&height)
                    .unwrap_or_else(|| env::panic_str(ERR_KEY_NOT_EXIST));

                self.remove_block_header(blockhash);
                self.mainchain_height_to_header.remove(&height);
            }

            self.mainchain_initial_blockhash = self
                .mainchain_height_to_header
                .get(&end_removal_height)
                .unwrap_or_else(|| env::panic_str(ERR_KEY_NOT_EXIST));
        }
    }
```
