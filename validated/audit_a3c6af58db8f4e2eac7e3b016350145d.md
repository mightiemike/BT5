### Title
`gc_threshold` set at initialization cannot be adjusted post-deployment, permanently constraining SPV confirmation depth and enabling reorg-induced contract freeze — (File: `contract/src/lib.rs`)

---

### Summary

`gc_threshold` is written once during `init` and has no privileged setter. Because it simultaneously caps how many blocks GC retains and enforces the maximum `confirmations` value accepted by `verify_transaction_inclusion`, an operator who needs to raise or lower the threshold after deployment cannot do so without a full contract redeployment. If the value is set too low, high-confirmation SPV proofs are permanently rejected; if a chain reorganization reaches deeper than the retained window, the contract panics and becomes permanently unable to accept new blocks.

---

### Finding Description

`gc_threshold` is stored as a plain `u64` field on `BtcLightClient`: [1](#0-0) 

It is assigned exactly once, from the caller-supplied `InitArgs`, during `init`: [2](#0-1) 

No public or role-gated setter for `gc_threshold` exists anywhere in the contract source. A grep across all `contract/src/*.rs` files returns only the field declaration, the single init assignment, and the two use-sites — no mutation after construction.

The two use-sites that create concrete impact are:

**1. `verify_transaction_inclusion` hard-caps `confirmations` at `gc_threshold`:** [3](#0-2) 

Any caller requesting more confirmations than the frozen threshold receives a permanent hard rejection. Because `verify_transaction_inclusion_v2` delegates to `verify_transaction_inclusion`, both entry points are affected. [4](#0-3) 

**2. `run_mainchain_gc` prunes blocks down to `gc_threshold`:** [5](#0-4) 

If `gc_threshold` is set too small, GC removes ancestor blocks that `reorg_chain` needs to walk back to the common ancestor. The CLAUDE.md documentation explicitly acknowledges this: *"If mainchain blocks near the fork point have been garbage collected, reorg will fail — the contract panics with `PrevBlockNotFound`."* [6](#0-5) 

When that panic fires inside `submit_blocks`, the transaction reverts but the chain state is left at the pre-reorg tip. Every subsequent attempt to submit the winning fork chain re-triggers the same panic, permanently freezing the contract.

---

### Impact Explanation

- **SPV proof consumers** calling `verify_transaction_inclusion` or `verify_transaction_inclusion_v2` with `confirmations > gc_threshold` receive a permanent hard rejection. This is the primary security service the contract provides; a frozen confirmation ceiling defeats the purpose of the light client for high-value use cases.
- **Chain continuity**: if a reorganization deeper than `gc_threshold` occurs (e.g., after a selfish-mining event or a long network partition), `submit_blocks` panics on every attempt to submit the winning fork, leaving the contract permanently stuck on the stale chain tip with no recovery path short of a full redeployment.

---

### Likelihood Explanation

Operators commonly set `gc_threshold` conservatively low to control NEAR storage costs (the relayer example config uses `10000`; the recommended value is `52704`). Bitcoin's longest observed reorgs are shallow, but Dogecoin and Litecoin (also supported) have historically seen deeper reorgs. Any operator who later needs to raise the confirmation ceiling for a downstream bridge or who encounters a reorg deeper than their chosen threshold has no on-chain remedy.

---

### Recommendation

Add a DAO-gated setter, mirroring the pattern used for other adjustable parameters in comparable protocols:

```rust
pub fn set_gc_threshold(&mut self, gc_threshold: u64) {
    // require DAO role via near-plugins access control
    self.assert_acl_role(Role::DAO);
    self.gc_threshold = gc_threshold;
}
```

This allows the threshold to be raised when downstream consumers require more confirmations, or lowered when storage costs must be reduced, without redeployment.

---

### Proof of Concept

**Scenario A — permanent SPV rejection:**

1. Deploy contract with `gc_threshold = 6`.
2. Any NEAR account calls `verify_transaction_inclusion_v2` with `confirmations = 7`.
3. The call always panics: *"The required number of confirmations exceeds the number of blocks stored in memory"* — regardless of how many blocks are actually stored.
4. There is no on-chain call that can raise `gc_threshold` to unblock the caller.

**Scenario B — reorg-induced contract freeze:**

1. Deploy contract with `gc_threshold = 10`.
2. Relayer submits 20 mainchain blocks; GC prunes the oldest 10, retaining blocks 11–20.
3. A 12-block fork rooted at block 8 (now GC'd) accumulates more chainwork.
4. Relayer calls `submit_blocks` with the fork tip.
5. `reorg_chain` walks back through the fork looking for the common ancestor; it reaches block 8, calls `headers_pool.get(block_8_hash)`, gets `None`, and panics with `PrevBlockNotFound`.
6. Every subsequent `submit_blocks` call for any block on the winning fork re-triggers the same panic. The contract is permanently frozen on the stale chain, and `gc_threshold` cannot be raised to recover. [7](#0-6)

### Citations

**File:** contract/src/lib.rs (L113-114)
```rust
    // GC threshold - how many blocks we would like to store in memory, and GC the older ones
    gc_threshold: u64,
```

**File:** contract/src/lib.rs (L135-144)
```rust
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
```

**File:** contract/src/lib.rs (L288-292)
```rust
    pub fn verify_transaction_inclusion(&self, #[serializer(borsh)] args: ProofArgs) -> bool {
        require!(
            args.confirmations <= self.gc_threshold,
            "The required number of confirmations exceeds the number of blocks stored in memory"
        );
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

**File:** contract/src/lib.rs (L391-393)
```rust
        if amount_of_headers_we_store > self.gc_threshold {
            let total_amount_to_remove = amount_of_headers_we_store - self.gc_threshold;
            let selected_amount_to_remove = std::cmp::min(total_amount_to_remove, batch_size);
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

**File:** contract/CLAUDE.md (L60-60)
```markdown
**Caveat**: If mainchain blocks near the fork point have been garbage collected, reorg will fail — the contract panics with `PrevBlockNotFound` when it cannot walk the chain back to the common ancestor. This means GC depth must be set conservatively relative to expected fork lengths
```
