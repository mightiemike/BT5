### Title
NEAR Storage Deposits Permanently Locked After GC Frees Block Header Storage - (File: contract/src/lib.rs)

### Summary
`submit_blocks` is `#[payable]` and collects NEAR deposits to cover on-chain storage costs for block headers. When `run_mainchain_gc` later frees that storage, the NEAR tokens deposited in prior calls remain permanently locked in the contract. No withdrawal function exists anywhere in the contract.

### Finding Description
`submit_blocks` measures the net storage delta of the current call and refunds only the excess from the deposit attached to that same call: [1](#0-0) 

The refund at line 190–194 is scoped entirely to `amount` (the current call's `attached_deposit`). It has no knowledge of, and makes no attempt to return, NEAR tokens deposited in earlier calls.

`run_mainchain_gc` removes old mainchain block headers from all three storage maps: [2](#0-1) 

When those entries are deleted, NEAR Protocol releases the storage staking obligation proportional to the freed bytes, and that NEAR becomes free balance in the contract account. However, the contract has no function to transfer this balance out — no `withdraw`, no admin drain, nothing. Searching the entire `contract/src/lib.rs` confirms zero withdrawal methods exist. [3](#0-2) 

The `reorg_chain` path also removes headers via `remove_block_header` without any deposit accounting: [4](#0-3) 

### Impact Explanation
Every NEAR token deposited by a relayer to pay for block header storage is permanently locked once GC or a reorg removes those headers. With the recommended `gc_threshold = 52704` (≈ one year of Bitcoin blocks), the contract continuously accumulates freed storage deposits that can never be recovered. The contract's free balance grows monotonically with no exit path.

### Likelihood Explanation
This is a certainty under normal operation. GC runs automatically inside every `submit_blocks` call (`self.run_mainchain_gc(num_of_headers)` at line 181). Once the mainchain exceeds `gc_threshold`, every subsequent `submit_blocks` call frees storage from prior paid-for headers while the corresponding NEAR remains locked. The recommended production threshold of 52704 blocks means this condition is reached after roughly one year and then persists indefinitely. [5](#0-4) 

### Recommendation
Add a privileged withdrawal function (callable only by `Role::DAO` or a designated treasury role) that transfers the contract's free balance — i.e., `env::account_balance() - env::storage_byte_cost() * env::storage_usage()` — to a specified beneficiary. Alternatively, track cumulative storage deposits and refund the freed portion to the original depositor when GC removes their headers, though this requires per-block deposit accounting.

### Proof of Concept
1. Relayer calls `submit_blocks([h1..h100])` with a deposit of `D` NEAR. Contract stores 100 headers; `D` NEAR is consumed for storage staking.
2. Chain advances. Relayer calls `submit_blocks([h101..h200])` with another deposit. GC triggers inside this call and removes `h1..h85`. The storage delta for this call is `(+85 new) - (-85 GC'd) = 0` net, so `required_deposit = 0` and the current call's deposit is fully refunded.
3. However, the `D` NEAR from step 1 that paid for `h1..h85` is now free balance in the contract — the storage obligation for those headers is gone, but the NEAR is not returned to the step-1 depositor.
4. No function in the contract can move this balance out. It is permanently locked. [6](#0-5)

### Citations

**File:** contract/src/lib.rs (L126-198)
```rust
impl BtcLightClient {
    /// Recommended initialization parameters:
    /// * `genesis_block_height % difficulty_adjustment_interval == 0`: The genesis block height must be divisible by `difficulty_adjustment_interval` to align with difficulty adjustment cycles.
    /// * The `genesis_block` must be at least 144 blocks earlier than the last block. 144 is the approximate number of blocks generated in one day.
    /// * `skip_pow_verification = false`: Should be set to `false` for standard use. Set to `true` only for testing purposes.
    /// * `gc_threshold = 52704`: This is the approximate number of blocks generated in a year.
    #[init]
    #[private]
    #[must_use]
    pub fn init(args: InitArgs) -> Self {
        let mut contract = Self {
            mainchain_height_to_header: LookupMap::new(StorageKey::MainchainHeightToHeader),
            mainchain_header_to_height: LookupMap::new(StorageKey::MainchainHeaderToHeight),
            headers_pool: LookupMap::new(StorageKey::HeadersPool),
            mainchain_initial_blockhash: H256::default(),
            mainchain_tip_blockhash: H256::default(),
            skip_pow_verification: args.skip_pow_verification,
            gc_threshold: args.gc_threshold,
            network: args.network,
        };

        // Make the contract itself super admin. This allows us to grant any role in the
        // constructor.
        near_sdk::require!(
            contract.acl_init_super_admin(env::current_account_id()),
            "Failed to initialize super admin",
        );

        contract.init_genesis(
            &args.genesis_block_hash,
            args.genesis_block_height,
            args.submit_blocks,
        );

        contract
    }

    /// This method submits provided headers
    /// # Panics
    /// Cannot parse headers len as u64
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

**File:** contract/src/lib.rs (L401-414)
```rust
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
