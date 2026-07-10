### Title
Storage Deposits Paid by Relayers Are Permanently Locked When Headers Are Garbage-Collected — (File: `contract/src/lib.rs`)

---

### Summary

`submit_blocks` requires relayers to attach NEAR tokens to cover the net storage cost of new block headers. When the GC mechanism (`run_mainchain_gc`) later removes old headers, the storage is freed and the corresponding NEAR tokens are released back into the contract's account balance — but they are never returned to the original depositors. There is no withdrawal function, no per-depositor accounting, and no designated recipient for these freed tokens. They accumulate permanently in the contract's balance with no recovery path.

---

### Finding Description

In `submit_blocks`, the deposit accounting is based on the **net** storage change after both insertion and GC complete in the same call:

```rust
let initial_storage = env::storage_usage();          // measured before insertion
for header in headers { self.submit_block_header(...); }  // storage grows
self.run_mainchain_gc(num_of_headers);               // storage shrinks (old headers removed)
let diff_storage_usage = env::storage_usage().saturating_sub(initial_storage); // NET delta
let required_deposit = env::storage_byte_cost().saturating_mul(diff_storage_usage.into());
let refund = amount.saturating_sub(required_deposit);
// only the current caller is refunded
``` [1](#0-0) 

When GC removes headers that were paid for by **previous** callers, the freed storage reduces `diff_storage_usage` for the **current** caller (potentially to zero via `saturating_sub`). The current caller pays less (or nothing), but the original depositors receive nothing back. The NEAR tokens they paid are now "unlocked" from storage staking and sit in the contract's available balance with no mechanism to recover them.

`run_mainchain_gc` removes entries from both `mainchain_height_to_header` and `headers_pool` (via `remove_block_header`), freeing the storage bytes that were originally paid for:

```rust
self.remove_block_header(blockhash);
self.mainchain_height_to_header.remove(&height);
``` [2](#0-1) 

The same storage-freeing-without-refund pattern occurs during chain reorganizations in `reorg_chain`, which also calls `remove_block_header` on displaced mainchain headers: [3](#0-2) 

Additionally, `run_mainchain_gc` is a public, permissionless entry point (when the contract is not paused), callable by any NEAR account: [4](#0-3) 

The contract has no withdrawal function, no per-depositor tracking, and no designated recipient for freed storage deposits. The `migrate` function also does not address this accumulated balance. [5](#0-4) 

---

### Impact Explanation

NEAR tokens paid by relayers for block header storage are permanently locked in the contract's balance once those headers are GC'd. Over the operational lifetime of the contract (configured with `gc_threshold = 52704` blocks per year), every relayer that paid storage deposits for headers that were subsequently GC'd loses those tokens with no recourse. The contract's own test confirms this is the intended steady-state behavior — "GC kicks in and subsequent batches can be submitted for free" — but the freed tokens are simply absorbed into the contract balance rather than returned to anyone: [6](#0-5) 

---

### Likelihood Explanation

This is not a theoretical edge case. GC runs automatically on every `submit_blocks` call once the stored chain length exceeds `gc_threshold`. For a production deployment tracking Bitcoin mainnet with `gc_threshold = 52704`, GC will trigger on every submission after the first year of operation. Every such call permanently locks the storage deposits of the relayers whose headers are removed. The entry path is the normal, expected relayer workflow.

---

### Recommendation

1. **Track deposits per submitter**: Maintain a `LookupMap<AccountId, NearToken>` recording how much each account has deposited for storage. When GC removes a header, credit the freed storage value back to the original depositor's balance and allow them to withdraw it.
2. **Alternatively, designate a recipient**: If per-depositor refunds are too complex, document explicitly that freed storage deposits are transferred to a designated DAO/treasury account, and implement that transfer in `run_mainchain_gc` and `reorg_chain`.
3. **At minimum, document the behavior**: The current code gives no indication that relayer deposits are permanently forfeited upon GC. This should be explicitly documented so relayers understand the economic model.

---

### Proof of Concept

1. Relayer A calls `submit_blocks` with 100 headers, attaches 1 NEAR to cover storage. The deposit is accepted; 100 headers are stored.
2. Bitcoin produces 100 more blocks. Relayer B calls `submit_blocks` with 100 new headers and attaches a deposit.
3. Inside the same call, `run_mainchain_gc(100)` removes Relayer A's 100 headers (chain now exceeds `gc_threshold`).
4. `diff_storage_usage = env::storage_usage().saturating_sub(initial_storage)` evaluates to `0` (GC freed as much as was added).
5. `required_deposit = 0`; Relayer B receives a full refund of their deposit.
6. Relayer A's 1 NEAR is now in the contract's available balance. There is no function to return it to Relayer A, transfer it to a DAO, or otherwise recover it. It is permanently locked.

### Citations

**File:** contract/src/lib.rs (L173-197)
```rust
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
```

**File:** contract/src/lib.rs (L376-377)
```rust
    #[pause(except(roles(Role::UnrestrictedRunGC)))]
    pub fn run_mainchain_gc(&mut self, batch_size: u64) {
```

**File:** contract/src/lib.rs (L401-409)
```rust
            for height in start_removal_height..end_removal_height {
                let blockhash = &self
                    .mainchain_height_to_header
                    .get(&height)
                    .unwrap_or_else(|| env::panic_str(ERR_KEY_NOT_EXIST));

                self.remove_block_header(blockhash);
                self.mainchain_height_to_header.remove(&height);
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

**File:** contract/src/lib.rs (L726-751)
```rust
        pub fn migrate() -> Self {
            let raw_state = env::storage_read(b"STATE")
                .unwrap_or_else(|| env::panic_str("contract state not found"));

            if let Ok(state) = <Self as BorshDeserialize>::try_from_slice(&raw_state) {
                log!("state is already in the current layout");
                return state;
            }

            if let Ok(old_state) = BtcLightClientV2::try_from_slice(&raw_state) {
                log!("migrating state from the V2 layout");
                return Self {
                    mainchain_height_to_header: old_state.mainchain_height_to_header,
                    mainchain_header_to_height: old_state.mainchain_header_to_height,
                    mainchain_tip_blockhash: old_state.mainchain_tip_blockhash,
                    mainchain_initial_blockhash: old_state.mainchain_initial_blockhash,
                    headers_pool: old_state.headers_pool,
                    skip_pow_verification: old_state.skip_pow_verification,
                    gc_threshold: old_state.gc_threshold,
                    network: old_state.network,
                };
            }

            env::panic_str("contract state matches no known layout")
        }
    }
```

**File:** contract/tests/test_basics.rs (L533-537)
```rust
    async fn test_payment_on_block_submission() -> Result<(), Box<dyn std::error::Error>> {
        // gc_threshold=200: init (12 blocks) is well below threshold, so the first few
        // batches require deposit. After 3 batches with deposit (~12+85+85+85=267 total),
        // GC kicks in and subsequent batches can be submitted for free.
        let (contract, user_account, block_headers) = init_contract_from_file(200).await?;
```
