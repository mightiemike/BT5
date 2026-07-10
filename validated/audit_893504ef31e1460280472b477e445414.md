### Title
Storage Deposit Refund Missing on GC — NEAR Tokens Permanently Locked in Contract - (File: `contract/src/lib.rs`)

### Summary

`submit_blocks` collects NEAR storage deposits from relayers to pay for on-chain header storage. When `run_mainchain_gc` later deletes those headers, the storage staking requirement decreases and the previously locked NEAR tokens are freed back into the contract's own balance. Because the contract exposes no withdrawal function, those freed tokens are permanently trapped.

### Finding Description

`submit_blocks` is marked `#[payable]` and enforces that the caller attaches enough NEAR to cover the net storage increase caused by the new headers: [1](#0-0) 

The sequence inside the function is:

1. Record `initial_storage` (line 174).
2. Insert all new headers into `headers_pool` and the two mainchain maps.
3. Call `run_mainchain_gc` (line 181), which deletes old headers from the same maps.
4. Compute `diff_storage_usage = env::storage_usage().saturating_sub(initial_storage)` — the **net** delta (line 182).
5. Require `amount >= required_deposit` and refund only the surplus (lines 185–197).

Because the net delta is used, the deposit retained by the contract exactly covers the **net** storage increase of this single call. However, the headers deleted by GC were paid for by **previous** `submit_blocks` callers. When those headers are removed, NEAR Protocol automatically unlocks the corresponding storage-staking tokens back into the contract's balance. The contract never forwards those freed tokens to anyone.

`run_mainchain_gc` only removes storage entries; it performs no token accounting whatsoever: [2](#0-1) 

A search of the entire contract confirms there is no `withdraw`, `claim_storage_refund`, or any other function that transfers NEAR out of the contract to an external account. The only outbound `Promise::transfer` in the codebase is the per-call excess refund inside `submit_blocks` itself: [3](#0-2) 

### Impact Explanation

**Impact: High.** Every header that is garbage-collected releases storage-staking NEAR back into the contract's balance. Over the operational lifetime of the contract — which is designed to run indefinitely, continuously adding and GC-ing headers — the cumulative amount of locked NEAR grows without bound. No privileged role (DAO, PauseManager, RelayerManager, etc.) has a path to recover these tokens; the contract simply has no such function.

### Likelihood Explanation

**Likelihood: High.** GC is triggered automatically on every `submit_blocks` call (line 181) once the stored chain exceeds `gc_threshold`. For a production deployment tracking Bitcoin mainnet with `gc_threshold = 52704` (~1 year of blocks), GC fires on every batch submission after the first year. This is the normal, expected operating mode of the contract.

### Recommendation

Add a storage-refund accounting mechanism. Two complementary approaches:

1. **Track per-depositor balances**: Record how much each relayer deposited for storage. When GC frees storage, credit the freed amount back to the depositor's internal balance and expose a `withdraw_storage_deposit()` function.

2. **Simpler alternative — measure freed storage in `run_mainchain_gc`**: Capture `env::storage_usage()` before and after GC, compute the freed bytes, and transfer `freed_bytes * env::storage_byte_cost()` to a designated treasury or the caller.

### Proof of Concept

1. Relayer A calls `submit_blocks` with 100 headers, attaching the required deposit D₁. The contract stores 100 headers; D₁ stays in the contract.
2. The chain grows past `gc_threshold`. On the next `submit_blocks` call, `run_mainchain_gc` deletes the 100 headers Relayer A paid for. NEAR Protocol returns D₁ to the contract's balance.
3. The net storage delta for this new call is computed **after** GC, so the new caller is only charged for the net increase. D₁ is now sitting in the contract's balance with no owner and no withdrawal path.
4. Repeat indefinitely. The contract accumulates NEAR proportional to `gc_threshold * avg_header_size * storage_byte_cost` — permanently inaccessible.

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

**File:** contract/src/lib.rs (L401-415)
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
        }
```
