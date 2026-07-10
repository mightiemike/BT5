### Title
Anyone Can Trigger Aggressive Mainchain Garbage Collection, Breaking SPV Proof Verification - (File: `contract/src/lib.rs`)

---

### Summary

`run_mainchain_gc` is a public, state-mutating function with no caller authentication. Any unprivileged NEAR account can call it with `batch_size = u64::MAX` to immediately prune all mainchain block headers beyond `gc_threshold`. This causes `verify_transaction_inclusion` and `verify_transaction_inclusion_v2` to panic for any block that was prematurely removed, breaking SPV proof verification for all downstream consumers.

---

### Finding Description

The `#[pause(except(roles(Role::UnrestrictedRunGC)))]` decorator on `run_mainchain_gc` only controls whether the function is callable when the contract is **paused**. It performs no authentication of the caller when the contract is running normally. The function is therefore callable by any NEAR account with no role requirement:

```rust
// contract/src/lib.rs:376-416
#[pause(except(roles(Role::UnrestrictedRunGC)))]
pub fn run_mainchain_gc(&mut self, batch_size: u64) {
    ...
    if amount_of_headers_we_store > self.gc_threshold {
        let total_amount_to_remove = amount_of_headers_we_store - self.gc_threshold;
        let selected_amount_to_remove = std::cmp::min(total_amount_to_remove, batch_size);
        ...
        for height in start_removal_height..end_removal_height {
            self.remove_block_header(blockhash);
            self.mainchain_height_to_header.remove(&height);
        }
        self.mainchain_initial_blockhash = ...;
    }
}
```

By contrast, `submit_blocks` — the only intended caller of `run_mainchain_gc` — is gated by `#[trusted_relayer]`, which enforces that the caller holds `Role::UnrestrictedSubmitBlocks` or `Role::DAO`:

```rust
// contract/src/lib.rs:167-181
#[payable]
#[pause]
#[trusted_relayer]
pub fn submit_blocks(...) -> PromiseOrValue<()> {
    ...
    self.run_mainchain_gc(num_of_headers);
    ...
}
```

The internal call from `submit_blocks` passes `batch_size = num_of_headers` (the count of headers in that batch), which is a small, bounded value. An attacker calling `run_mainchain_gc` directly can pass `u64::MAX`, immediately removing every mainchain block header beyond `gc_threshold` in a single transaction.

After GC, `verify_transaction_inclusion` panics for any removed block:

```rust
// contract/src/lib.rs:299-301
let target_block_height = self
    .mainchain_header_to_height
    .get(&args.tx_block_blockhash)
    .unwrap_or_else(|| env::panic_str("block does not belong to the current main chain"));
```

And `verify_transaction_inclusion_v2` delegates to the same path:

```rust
// contract/src/lib.rs:367-368
#[allow(deprecated)]
self.verify_transaction_inclusion(args.into())
```

---

### Impact Explanation

Any downstream NEAR contract or user relying on `verify_transaction_inclusion` or `verify_transaction_inclusion_v2` for cross-chain bridge settlement, payment verification, or custody release will receive a panic (transaction failure) for any block that was prematurely GC'd. The attacker can repeat the call after each new batch of blocks is submitted, keeping the mainchain perpetually trimmed to exactly `gc_threshold` blocks and invalidating any SPV proof for blocks older than that window. Additionally, as documented in `contract/CLAUDE.md`, chain reorg resolution panics with `PrevBlockNotFound` if the common ancestor was GC'd, corrupting the canonical chain tracking.

---

### Likelihood Explanation

The entry point is a public NEAR contract method callable by any account. No stake, deposit, or special credential is required beyond gas. The attack is cheap, repeatable, and requires no knowledge beyond the contract's ABI. Any actor wishing to disrupt cross-chain settlement (e.g., to prevent a bridge withdrawal from being finalized) has a direct, low-cost mechanism.

---

### Recommendation

Add an explicit role check to `run_mainchain_gc` so that only privileged callers (e.g., `Role::DAO` or a new dedicated `GCManager` role) can invoke it directly. The internal call from `submit_blocks` is already protected by `#[trusted_relayer]` and does not need to change. For example:

```rust
#[pause(except(roles(Role::UnrestrictedRunGC)))]
pub fn run_mainchain_gc(&mut self, batch_size: u64) {
    require!(
        self.acl_has_role(Role::DAO, &env::predecessor_account_id())
            || self.acl_has_role(Role::UnrestrictedRunGC, &env::predecessor_account_id()),
        "Unauthorized: caller lacks GC role"
    );
    ...
}
```

Alternatively, make `run_mainchain_gc` a private function and expose a separate, role-gated public wrapper.

---

### Proof of Concept

1. Deploy the contract with `gc_threshold = 52704` and submit 60,000 blocks via the authorized relayer. The mainchain now holds 60,000 headers; 7,296 are beyond the threshold but have not yet been GC'd (normal GC removes only `num_of_headers` per `submit_blocks` call).
2. Any unprivileged NEAR account calls:
   ```
   run_mainchain_gc({ "batch_size": 18446744073709551615 })
   ```
3. The contract immediately removes all 7,296 excess blocks in one transaction, advancing `mainchain_initial_blockhash` to block 7,296.
4. Any subsequent call to `verify_transaction_inclusion` for a block at height < 7,296 panics: `"block does not belong to the current main chain"`.
5. The attacker repeats after each new relayer submission to keep the window maximally trimmed. [1](#0-0) [2](#0-1) [3](#0-2) [4](#0-3) [5](#0-4)

### Citations

**File:** contract/src/lib.rs (L120-124)
```rust
#[trusted_relayer(
    bypass_roles(Role::DAO, Role::UnrestrictedSubmitBlocks),
    manager_roles(Role::DAO, Role::RelayerManager),
    config_roles(Role::DAO)
)]
```

**File:** contract/src/lib.rs (L167-181)
```rust
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
```

**File:** contract/src/lib.rs (L299-301)
```rust
            .mainchain_header_to_height
            .get(&args.tx_block_blockhash)
            .unwrap_or_else(|| env::panic_str("block does not belong to the current main chain"));
```

**File:** contract/src/lib.rs (L376-377)
```rust
    #[pause(except(roles(Role::UnrestrictedRunGC)))]
    pub fn run_mainchain_gc(&mut self, batch_size: u64) {
```

**File:** contract/src/lib.rs (L391-414)
```rust
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
```
