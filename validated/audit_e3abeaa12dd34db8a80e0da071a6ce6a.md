### Title
NEAR Storage Deposit Permanently Locked After Garbage Collection — (`contract/src/lib.rs`)

### Summary

The `BtcLightClient` contract accepts NEAR token deposits via `submit_blocks` to cover on-chain storage costs. When the garbage collector (`run_mainchain_gc`) later frees that storage, the NEAR tokens originally paid for it are never returned. No withdrawal or rescue function exists anywhere in the contract, so these tokens accumulate permanently in the contract's balance with no recovery path.

### Finding Description

`submit_blocks` is marked `#[payable]` and charges callers for the net storage increase within each call:

```rust
// contract/src/lib.rs:173-197
let amount = env::attached_deposit();
let initial_storage = env::storage_usage();
// ... store new headers ...
self.run_mainchain_gc(num_of_headers);
let diff_storage_usage = env::storage_usage().saturating_sub(initial_storage);
let required_deposit = env::storage_byte_cost().saturating_mul(diff_storage_usage.into());
require!(amount >= required_deposit, ...);
let refund = amount.saturating_sub(required_deposit);
if refund > NearToken::from_near(0) {
    Promise::new(env::predecessor_account_id()).transfer(refund).into()
}
```

The deposit accounting is per-call and only refunds the excess above the **current call's** net storage delta. It does not account for storage freed in **future** calls.

`run_mainchain_gc` deletes old mainchain headers from storage:

```rust
// contract/src/lib.rs:401-409
for height in start_removal_height..end_removal_height {
    let blockhash = &self.mainchain_height_to_header.get(&height)...;
    self.remove_block_header(blockhash);
    self.mainchain_height_to_header.remove(&height);
}
```

When GC removes blocks, the on-chain storage shrinks and the contract's minimum required balance (storage staking) decreases. The NEAR that was deposited in prior calls to pay for those now-deleted blocks remains in the contract's balance as permanently unrecoverable surplus. No `withdraw`, `rescue_near`, or equivalent function exists anywhere in the contract — confirmed by a full search of `contract/src/`.

### Impact Explanation

Every block that passes through the GC cycle represents NEAR that was deposited for storage and never returned. With the recommended `gc_threshold = 52704` (≈ one year of Bitcoin blocks) and a deposit of ~500 milliNEAR per batch of 85 blocks, the contract continuously accumulates freed-storage NEAR. Over the operational lifetime of the contract, this compounds into a material sum locked with no on-chain recovery mechanism. The DAO, relayer operators, or any other party have no way to retrieve these funds.

### Likelihood Explanation

GC is triggered automatically on every `submit_blocks` call once the mainchain exceeds `gc_threshold`. Because the contract is designed to run indefinitely as a live relay, GC will fire continuously in steady-state operation. The accumulation is therefore not a corner case — it is the normal operating mode of a long-running deployment.

### Recommendation

Add a privileged `rescue_near` (or `withdraw_surplus`) function callable only by the DAO role, analogous to the pattern recommended in the reference report:

```rust
#[payable]
pub fn rescue_near(&mut self, amount: NearToken, receiver_id: AccountId) -> Promise {
    // require DAO role
    Promise::new(receiver_id).transfer(amount)
}
```

Alternatively, track cumulative storage deposits and refund the proportional share to the original depositor when GC frees their blocks — though this is significantly more complex.

### Proof of Concept

1. Relayer submits batch of 85 headers with `deposit = 500 milliNEAR`. Contract stores 85 headers; net storage increases; deposit is consumed. Contract balance += 500 milliNEAR.
2. Relayer submits the next batch of 85 headers. GC fires (`run_mainchain_gc(85)`), deleting the 85 headers from step 1. Net storage delta for this call = +85 new − 85 removed = 0. `required_deposit = 0`. Full deposit for this call is refunded.
3. The 500 milliNEAR from step 1 remains in the contract. The storage it paid for no longer exists. No function can retrieve it.
4. This cycle repeats every batch in steady-state. After one year at the recommended `gc_threshold`, the locked surplus equals the total NEAR paid for all GC'd blocks — with zero recovery path.

**Root cause lines:**
- Deposit charged only against current-call net delta: [1](#0-0) 
- GC frees storage with no NEAR return: [2](#0-1) 
- No withdrawal function exists anywhere in the contract source:

### Citations

**File:** contract/src/lib.rs (L182-183)
```rust
        let diff_storage_usage = env::storage_usage().saturating_sub(initial_storage);
        let required_deposit = env::storage_byte_cost().saturating_mul(diff_storage_usage.into());
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
