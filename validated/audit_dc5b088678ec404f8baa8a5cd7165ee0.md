### Title
Accumulated NEAR Storage Deposits Permanently Locked in Contract After GC — (`contract/src/lib.rs`)

---

### Summary

`submit_blocks` is `#[payable]` and collects NEAR deposits to cover on-chain storage costs. When `run_mainchain_gc` removes old block headers (either inline inside `submit_blocks` or via its own public entry point), the storage previously paid for is freed and the corresponding NEAR tokens are unlocked into the contract's account balance. No withdrawal function exists for any role — including `DAO` or the super-admin — to recover these accumulated tokens. They are permanently locked in the contract.

---

### Finding Description

`submit_blocks` measures the net storage delta **after** GC has already run:

```rust
// contract/src/lib.rs  L174–L197
let initial_storage = env::storage_usage();          // snapshot before
for header in headers { self.submit_block_header(…); }
self.run_mainchain_gc(num_of_headers);               // may remove many old headers
let diff_storage_usage = env::storage_usage()
    .saturating_sub(initial_storage);               // net delta (floor 0)
let required_deposit = env::storage_byte_cost()
    .saturating_mul(diff_storage_usage.into());
let refund = amount.saturating_sub(required_deposit);
if refund > NearToken::from_near(0) {
    Promise::new(env::predecessor_account_id()).transfer(refund).into()
} else { PromiseOrValue::Value(()) }
```

The refund logic is correct for the **current** call: if GC removes more bytes than the new headers add, `diff_storage_usage` saturates to 0, `required_deposit = 0`, and the full attached deposit is returned to the caller.

However, the NEAR tokens deposited in **previous** calls to cover the storage of the headers that GC just deleted are already sitting in the contract's account balance. In NEAR Protocol, when a contract's storage shrinks, the previously locked balance is unlocked and added to the contract's spendable balance — it is not automatically returned to any external account. Because the contract has no `withdraw`, `drain`, or similar admin function, those unlocked tokens accumulate indefinitely with no recovery path.

The same issue applies when `run_mainchain_gc` is called directly as a standalone public method:

```rust
// contract/src/lib.rs  L376–L416
#[pause(except(roles(Role::UnrestrictedRunGC)))]
pub fn run_mainchain_gc(&mut self, batch_size: u64) { … }
```

This entry point is callable by any unprivileged NEAR account when the contract is not paused. It removes headers and frees storage without any refund or accounting of the previously deposited NEAR.

A search of the entire `contract/src/` tree confirms there is no `withdraw`, `drain`, `recover`, or equivalent function on any role.

---

### Impact Explanation

Every batch of block headers submitted by the relayer requires a NEAR deposit proportional to the storage consumed. As the chain grows and GC periodically evicts old headers, the NEAR tokens that paid for those headers are unlocked into the contract's balance and cannot be retrieved. Over the operational lifetime of the contract (designed for a `gc_threshold` of ~52,704 blocks, roughly one year of Bitcoin headers), the cumulative locked amount grows proportionally to the total storage ever paid. These tokens are irrecoverable by any party — the relayer that paid them, the DAO, or the super-admin.

---

### Likelihood Explanation

GC runs automatically on every `submit_blocks` call once the stored chain exceeds `gc_threshold`. This is the normal, expected operational path. No adversarial action is required; the token accumulation is a deterministic consequence of routine relayer operation. Any authorized relayer (holding `UnrestrictedSubmitBlocks`) triggers it on every submission batch once the chain is mature.

---

### Recommendation

Add a privileged withdrawal function accessible only to the `DAO` role (or equivalent `DEFAULT_ADMIN`-equivalent role) that transfers the contract's excess spendable balance to a designated recipient:

```rust
pub fn withdraw_excess(&mut self, recipient: AccountId, amount: NearToken) {
    // require DAO role
    Promise::new(recipient).transfer(amount)
}
```

Alternatively, track the cumulative storage deposit per caller and issue pro-rata refunds when GC frees storage attributable to their submissions — though this is significantly more complex.

---

### Proof of Concept

1. Relayer submits 1,000 headers across 10 calls, each attaching 0.5 NEAR to cover storage. Contract balance increases by ~5 NEAR (net of refunds).
2. Chain matures past `gc_threshold`. On the 11th call, `run_mainchain_gc` removes 1,000 old headers. Storage freed ≈ storage added by new batch; `diff_storage_usage ≈ 0`; full current deposit refunded to relayer.
3. The ~5 NEAR from steps 1–10 is now unlocked in the contract's spendable balance.
4. No function in `contract/src/lib.rs` allows any account — including `DAO` — to transfer this balance out.
5. Repeat indefinitely: every GC cycle permanently locks another tranche of NEAR in the contract. [1](#0-0) [2](#0-1) [3](#0-2)

### Citations

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

**File:** contract/src/lib.rs (L376-416)
```rust
    #[pause(except(roles(Role::UnrestrictedRunGC)))]
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
