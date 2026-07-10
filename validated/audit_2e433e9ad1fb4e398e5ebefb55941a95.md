### Title
Unpermissioned `run_mainchain_gc` Allows Any Caller to Irreversibly Prune Mainchain Headers, Invalidating SPV Proofs — (`File: contract/src/lib.rs`)

---

### Summary

`run_mainchain_gc` is a public, state-mutating NEAR contract method that carries no caller authorization check. Any unprivileged NEAR account can invoke it at any time with an arbitrarily large `batch_size`, causing the contract to permanently delete the oldest mainchain block headers up to the full excess over `gc_threshold`. Once deleted, those headers cannot be recovered, and any downstream call to `verify_transaction_inclusion` or `verify_transaction_inclusion_v2` for a transaction in a pruned block will panic, returning a hard failure to the consuming contract.

---

### Finding Description

`run_mainchain_gc` is declared as a plain `pub fn` under the `#[near]` impl block and is decorated only with `#[pause(except(roles(Role::UnrestrictedRunGC)))]`. [1](#0-0) 

The `#[pause]` decorator only gates execution when the contract is in a paused state; it imposes no restriction on *who* may call the function when the contract is live. There is no `#[private]`, no role check, and no `env::predecessor_account_id()` guard anywhere in the function body. [2](#0-1) 

The function computes `total_amount_to_remove = amount_of_headers_we_store - gc_threshold` and then removes `min(total_amount_to_remove, batch_size)` headers. Passing `u64::MAX` as `batch_size` causes the full excess to be removed in a single call. For each removed height the function:

1. Calls `remove_block_header`, which deletes the entry from both `mainchain_header_to_height` and `headers_pool`.
2. Calls `mainchain_height_to_header.remove`.
3. Advances `mainchain_initial_blockhash` to the new oldest surviving block. [3](#0-2) 

These deletions are permanent. The relayer cannot re-submit a removed block because `submit_block_header` requires the previous block to already be present in `headers_pool`; re-inserting a pruned range would require re-submitting every intermediate block in order, which is not a supported operational path.

Contrast this with `submit_blocks`, which is protected by the `#[trusted_relayer]` macro and therefore restricted to staked/approved relayers: [4](#0-3) 

`run_mainchain_gc` receives no equivalent protection despite being equally capable of mutating critical contract state.

---

### Impact Explanation

After a successful attack, `verify_transaction_inclusion` panics at the `mainchain_header_to_height.get` call for any block whose header was pruned: [5](#0-4) 

`verify_transaction_inclusion_v2` delegates to the same path and is equally affected: [6](#0-5) 

Any cross-chain application (bridge, DeFi protocol, oracle) that calls either verification function for a transaction in a pruned block receives a hard panic instead of a boolean result. If the consuming contract treats a non-panic `false` as "unverified" and a panic as a contract-level failure, the attacker can selectively break verification for specific historical blocks while leaving others intact, enabling targeted denial of SPV proofs for chosen transactions.

---

### Likelihood Explanation

The attack requires only a standard NEAR account and enough NEAR tokens to cover gas. No staking, no privileged role, no leaked key, and no social engineering is needed. The function is callable at any time the contract is not paused. Because the contract is designed to run continuously with a live relayer, the paused state is not the normal operating condition. The attacker can monitor the chain, wait until `amount_of_headers_we_store > gc_threshold` (which is the normal steady-state once the chain has been running for `gc_threshold` blocks), and then call `run_mainchain_gc(u64::MAX)` to prune the maximum possible set of headers in one transaction.

---

### Recommendation

Add a caller authorization guard to `run_mainchain_gc`. The simplest approach consistent with the existing role model is to require the caller to hold `Role::UnrestrictedRunGC` (already defined for the pause-bypass case) or a dedicated `GCManager` role:

```rust
#[pause(except(roles(Role::UnrestrictedRunGC)))]
pub fn run_mainchain_gc(&mut self, batch_size: u64) {
    require!(
        self.acl_has_role(Role::UnrestrictedRunGC, &env::predecessor_account_id())
            || self.acl_has_role(Role::DAO, &env::predecessor_account_id()),
        "Caller is not authorized to run GC"
    );
    // ... existing body
}
```

Alternatively, make the function `#[private]` and expose GC only as an internal call from `submit_blocks`, which is already gated by `#[trusted_relayer]`. [7](#0-6) 

---

### Proof of Concept

**Precondition**: The contract has been running long enough that `tip_height - initial_height + 1 > gc_threshold` (normal steady-state).

**Attacker steps**:

1. Any NEAR account calls:
   ```
   near call <contract_id> run_mainchain_gc '{"batch_size": 18446744073709551615}' --accountId attacker.near
   ```
2. The contract computes `total_amount_to_remove = (tip_height - initial_height + 1) - gc_threshold` and removes that many headers from `headers_pool`, `mainchain_height_to_header`, and `mainchain_header_to_height`.
3. `mainchain_initial_blockhash` is advanced to the new oldest block.

**Verification**: A subsequent call to `verify_transaction_inclusion` for any transaction in a pruned block panics with `"block does not belong to the current main chain"`, confirming the SPV proof path is broken for those blocks. [8](#0-7)

### Citations

**File:** contract/src/lib.rs (L167-169)
```rust
    #[pause]
    #[trusted_relayer]
    pub fn submit_blocks(
```

**File:** contract/src/lib.rs (L180-181)
```rust

        self.run_mainchain_gc(num_of_headers);
```

**File:** contract/src/lib.rs (L298-301)
```rust
        let target_block_height = self
            .mainchain_header_to_height
            .get(&args.tx_block_blockhash)
            .unwrap_or_else(|| env::panic_str("block does not belong to the current main chain"));
```

**File:** contract/src/lib.rs (L347-368)
```rust
    pub fn verify_transaction_inclusion_v2(&self, #[serializer(borsh)] args: ProofArgsV2) -> bool {
        require!(
            args.merkle_proof.len() == args.coinbase_merkle_proof.len(),
            "Coinbase merkle proof and transaction merkle proof should have the same length"
        );

        let header = self
            .headers_pool
            .get(&args.tx_block_blockhash)
            .unwrap_or_else(|| env::panic_str("cannot find requested transaction block"));

        require!(
            merkle_tools::compute_root_from_merkle_proof(
                args.coinbase_tx_id.clone(),
                0usize,
                &args.coinbase_merkle_proof,
            ) == header.block_header.merkle_root,
            "Incorrect coinbase merkle proof"
        );

        #[allow(deprecated)]
        self.verify_transaction_inclusion(args.into())
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
