### Title
Unpermissioned `run_mainchain_gc` Allows Any Caller to Prematurely Purge Verifiable Block Headers, Permanently Breaking Transaction Inclusion Proofs — (`contract/src/lib.rs`)

---

### Summary

`run_mainchain_gc` is a public, state-mutating function with no caller access control. Any unprivileged NEAR account can invoke it with an arbitrarily large `batch_size`, immediately removing all GC-eligible block headers from the mainchain in a single transaction. This permanently destroys the ability to call `verify_transaction_inclusion` or `verify_transaction_inclusion_v2` for those headers, which can permanently lock funds in any downstream bridge or consumer contract that depends on those proofs.

---

### Finding Description

`run_mainchain_gc` is decorated only with `#[pause(except(roles(Role::UnrestrictedRunGC)))]`, which restricts calls only when the contract is **paused**. When the contract is live (the normal production state), **any NEAR account** can call it with any `batch_size` value. [1](#0-0) 

Internally, `submit_blocks` calls `run_mainchain_gc` with `batch_size = num_of_headers` — the count of headers submitted in that single call. This naturally throttles GC to a small, bounded removal per relayer transaction. [2](#0-1) 

An external caller is not subject to this throttle. By passing `batch_size = u64::MAX`, an attacker can remove every block currently eligible for GC — up to `amount_of_headers_we_store - gc_threshold` headers — in a single call, far ahead of the schedule the relayer would impose. [3](#0-2) 

The removed headers are deleted from both `mainchain_height_to_header` and `headers_pool` and from `mainchain_header_to_height`. Once removed, they are gone permanently. [4](#0-3) 

`verify_transaction_inclusion` looks up the target block in `mainchain_header_to_height` and panics with `"block does not belong to the current main chain"` if the entry is absent. [5](#0-4) 

`verify_transaction_inclusion_v2` delegates to the same path after its coinbase-proof check. [6](#0-5) 

---

### Impact Explanation

Any bridge or consumer contract that calls `verify_transaction_inclusion` / `verify_transaction_inclusion_v2` to gate fund releases relies on the target block remaining in the header pool. If an attacker calls `run_mainchain_gc(u64::MAX)` and removes that block before the consumer's cross-contract verification call executes, the verification panics and the consumer contract cannot release the funds. Because the block is permanently deleted from on-chain state, no retry will ever succeed. Funds locked behind that proof are permanently inaccessible.

The `confirmations` guard only checks `args.confirmations <= self.gc_threshold`; it does not guarantee the block is still present. A block can be within the `gc_threshold` window at the time a user initiates a proof but be removed by the attacker before the call lands. [7](#0-6) 

---

### Likelihood Explanation

The attack requires no special role, no stake, and no privileged key — only a standard NEAR account and a single contract call. The attacker can monitor the mempool for pending `verify_transaction_inclusion` calls and front-run them with `run_mainchain_gc(u64::MAX)`. The cost is a single gas fee. The attack is repeatable on every new batch of GC-eligible headers.

---

### Recommendation

Restrict `run_mainchain_gc` to trusted callers. The simplest fix is to add a role check analogous to the one already used on `submit_blocks`:

```rust
#[pause(except(roles(Role::UnrestrictedRunGC)))]
#[access_control(role_type(Role))]  // require Role::DAO or a new Role::GCManager
pub fn run_mainchain_gc(&mut self, batch_size: u64) { … }
```

Alternatively, make `run_mainchain_gc` private and remove its public entry point entirely, since it is already called internally by `submit_blocks`. [8](#0-7) 

---

### Proof of Concept

1. The contract is deployed with `gc_threshold = 52704`. The relayer has submitted 53000 headers; 296 are GC-eligible. A bridge contract is about to call `verify_transaction_inclusion` for a transaction in block at height `tip − 52800` (stored, but GC-eligible).

2. The attacker observes the pending bridge call and submits:
   ```
   run_mainchain_gc { batch_size: 18446744073709551615 }
   ```
   This removes all 296 eligible headers, including the target block, from `mainchain_height_to_header`, `mainchain_header_to_height`, and `headers_pool`.

3. The bridge's `verify_transaction_inclusion` call executes next. It calls `mainchain_header_to_height.get(&args.tx_block_blockhash)` and receives `None`, triggering the panic `"block does not belong to the current main chain"`.

4. The bridge's fund-release logic never executes. The block is gone permanently; no future retry can succeed. [8](#0-7) [9](#0-8)

### Citations

**File:** contract/src/lib.rs (L175-181)
```rust
        let num_of_headers = headers.len().try_into().unwrap();

        for header in headers {
            self.submit_block_header(header, self.skip_pow_verification);
        }

        self.run_mainchain_gc(num_of_headers);
```

**File:** contract/src/lib.rs (L289-313)
```rust
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

**File:** contract/src/lib.rs (L659-662)
```rust
    fn remove_block_header(&mut self, header_block_hash: &H256) {
        self.mainchain_header_to_height.remove(header_block_hash);
        self.headers_pool.remove(header_block_hash);
    }
```
