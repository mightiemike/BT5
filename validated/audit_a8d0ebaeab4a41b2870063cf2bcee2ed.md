### Title
Stale Mainchain State After Unpause Enables False-Positive Transaction Inclusion Verification — (`contract/src/lib.rs`)

---

### Summary

The `BtcLightClient` contract uses `near_plugins::Pausable` to gate both `submit_blocks` and `verify_transaction_inclusion*`. When the contract is unpaused, both functions become callable simultaneously. However, the relayer must submit a potentially large backlog of BTC headers before the on-chain mainchain state reflects reality. During the window between unpause and relayer catch-up, any caller can invoke `verify_transaction_inclusion` or `verify_transaction_inclusion_v2` against a stale — and potentially reorg-invalidated — mainchain state, receiving a `true` result for a transaction that no longer exists on the canonical BTC chain.

---

### Finding Description

The contract enforces a pause on all state-changing and verification operations:

- `submit_blocks` is gated by `#[pause]` and `#[trusted_relayer]` [1](#0-0) 
- `verify_transaction_inclusion` is gated by `#[pause]` [2](#0-1) 
- `verify_transaction_inclusion_v2` is gated by `#[pause]` [3](#0-2) 

When the `PauseManager` unpauses the contract, all three functions become callable in the same NEAR block. The verification functions immediately read from `mainchain_header_to_height` and `headers_pool` to determine whether a block is on the mainchain and whether enough confirmations exist:

```rust
let target_block_height = self
    .mainchain_header_to_height
    .get(&args.tx_block_blockhash)
    .unwrap_or_else(|| env::panic_str("block does not belong to the current main chain"));

require!(
    (heaviest_block_header.block_height).saturating_sub(target_block_height) + 1
        >= args.confirmations,
    "Not enough blocks confirmed"
);
``` [4](#0-3) 

Both the mainchain membership check and the confirmation count are computed against the contract's frozen, pre-pause state. There is no staleness guard — no timestamp comparison, no block-age check, no minimum tip-height requirement.

During a pause of any meaningful duration (hours to days), the real BTC chain continues to produce blocks. A chain reorganization — even a shallow one — can remove a previously confirmed transaction from the canonical chain. The contract's `mainchain_height_to_header` and `mainchain_header_to_height` maps still contain the pre-reorg blocks. [5](#0-4) 

The reorg is only applied to contract state when the relayer calls `submit_blocks` with the new chain, which triggers `reorg_chain` to demote old mainchain blocks and promote fork blocks. [6](#0-5) 

Until that `submit_blocks` call lands, the contract's mainchain is a lie: it still maps the reorged block hash to a height, and `verify_transaction_inclusion` returns `true` for a transaction that is no longer on the real BTC chain.

---

### Impact Explanation

Consumer contracts (atomic swaps, bridges, cross-chain settlement layers) that call `verify_transaction_inclusion` or `verify_transaction_inclusion_v2` immediately after unpause can be deceived into accepting a false-positive inclusion proof. A transaction that was double-spent or reorganized away during the pause period will still appear confirmed. Any value release, asset mint, or state transition gated on this verification result can be exploited.

---

### Likelihood Explanation

- BTC reorgs of 1–3 blocks occur regularly; during a multi-hour pause they are near-certain.
- The unpause event is publicly observable on NEAR. An attacker monitoring both chains can prepare the call in advance.
- `verify_transaction_inclusion` has no access restriction — any NEAR account can call it. [2](#0-1) 
- The relayer's `submit_blocks` requires a deposit for storage and must process potentially hundreds of headers, making it slower than a simple view-then-act call. [7](#0-6) 
- The `UnrestrictedSubmitBlocks` role can bypass the pause for submission, but this is a privileged role not held by the standard relayer, so the backlog still accumulates. [8](#0-7) 

---

### Recommendation

Add a post-unpause grace period during which `verify_transaction_inclusion` and `verify_transaction_inclusion_v2` remain blocked, giving the relayer time to submit the backlog and bring the contract's mainchain state up to date before verification is re-enabled. Concretely:

1. Record the NEAR block timestamp at unpause time in contract state.
2. In both verification functions, require that `env::block_timestamp() >= unpause_timestamp + GRACE_PERIOD` before proceeding.
3. Set `GRACE_PERIOD` to a value sufficient for the relayer to submit all missed headers (e.g., 30–60 minutes expressed in nanoseconds).

Alternatively, expose a `get_last_block_header().block_header.time` staleness check so consumer contracts can self-guard, and document the post-unpause risk prominently.

---

### Proof of Concept

**Setup:**
- Contract is initialized and synced to BTC block N (tip = `hash_N`).
- Transaction `T` is included in block `N-5`, with 6 confirmations visible to the contract.

**Attack sequence:**

1. `PauseManager` pauses the contract. `submit_blocks` and `verify_transaction_inclusion` are both blocked.
2. On the real BTC network, a 6-block reorg occurs. Block `N-5` (containing `T`) is replaced; `T` is double-spent in the new chain. The real canonical tip is now `hash_N'` at height `N+10`.
3. `PauseManager` unpauses the contract. The contract's state is still frozen at tip `hash_N`; `mainchain_header_to_height[N-5] = hash_{N-5}` still exists.
4. **Attacker** immediately calls `verify_transaction_inclusion` with `tx_block_blockhash = hash_{N-5}`, `confirmations = 6`, and a valid Merkle proof for `T`.
5. The contract checks: `mainchain_header_to_height.get(hash_{N-5})` → returns height `N-5` (stale, not yet reorged). [9](#0-8) 
6. Confirmation check: `(N - (N-5)) + 1 = 6 >= 6` → passes. [10](#0-9) 
7. Merkle proof is valid (the block header is unchanged). Function returns `true`.
8. Attacker's consumer contract releases funds based on the false-positive result.
9. Relayer later calls `submit_blocks` with the new chain, triggering `reorg_chain`, which removes `hash_{N-5}` from the mainchain — but the damage is already done. [11](#0-10)

### Citations

**File:** contract/src/lib.rs (L43-44)
```rust
    /// Allows to use contract API even after contract is paused
    UnrestrictedSubmitBlocks,
```

**File:** contract/src/lib.rs (L96-118)
```rust
pub struct BtcLightClient {
    // A pair of lookup maps that allows to find header by height and height by header
    mainchain_height_to_header: LookupMap<u64, H256>,
    mainchain_header_to_height: LookupMap<H256, u64>,

    // Block with the highest chainWork, i.e., blockchain tip, you can find latest height inside of it
    mainchain_tip_blockhash: H256,

    // The oldest block in main chain we store
    mainchain_initial_blockhash: H256,

    // Mapping of block hashes to block headers (ALL ever submitted, i.e., incl. forks)
    headers_pool: LookupMap<H256, ExtendedHeader>,

    // If we should run all the block checks or not
    skip_pow_verification: bool,

    // GC threshold - how many blocks we would like to store in memory, and GC the older ones
    gc_threshold: u64,

    // Network type Mainnet/Testnet
    network: Network,
}
```

**File:** contract/src/lib.rs (L167-169)
```rust
    #[pause]
    #[trusted_relayer]
    pub fn submit_blocks(
```

**File:** contract/src/lib.rs (L182-188)
```rust
        let diff_storage_usage = env::storage_usage().saturating_sub(initial_storage);
        let required_deposit = env::storage_byte_cost().saturating_mul(diff_storage_usage.into());

        require!(
            amount >= required_deposit,
            format!("Required deposit {}", required_deposit)
        );
```

**File:** contract/src/lib.rs (L287-288)
```rust
    #[pause]
    pub fn verify_transaction_inclusion(&self, #[serializer(borsh)] args: ProofArgs) -> bool {
```

**File:** contract/src/lib.rs (L298-308)
```rust
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
```

**File:** contract/src/lib.rs (L346-347)
```rust
    #[pause]
    pub fn verify_transaction_inclusion_v2(&self, #[serializer(borsh)] args: ProofArgsV2) -> bool {
```

**File:** contract/src/lib.rs (L562-566)
```rust
            // Current chainwork is higher than on a current mainchain, let's promote the fork
            if current_header.chain_work > total_main_chain_chainwork {
                log!("Chain reorg");
                self.reorg_chain(current_header, last_main_chain_block_height);
            }
```

**File:** contract/src/lib.rs (L574-646)
```rust
    /// The most expensive operation which reorganizes the chain, based on fork weight
    fn reorg_chain(&mut self, fork_tip_header: ExtendedHeader, last_main_chain_block_height: u64) {
        let fork_tip_height = fork_tip_header.block_height;
        if last_main_chain_block_height > fork_tip_height {
            // If we see that main chain is longer than fork we first garbage collect
            // outstanding main chain blocks:
            //
            //      [m1] - [m2] - [m3] - [m4] <- We should remove [m4]
            //     /
            // [m0]
            //     \
            //      [f1] - [f2] - [f3]
            for height in (fork_tip_height + 1)..=last_main_chain_block_height {
                let current_main_chain_blockhash = self
                    .mainchain_height_to_header
                    .get(&height)
                    .unwrap_or_else(|| env::panic_str("cannot get a block"));
                self.remove_block_header(&current_main_chain_blockhash);
                self.mainchain_height_to_header.remove(&height);
            }
        }

        // Now we are in a situation where mainchain is equivalent to fork size:
        //
        //      [m1] - [m2] - [m3] - [m4] <- main tip
        //     /
        // [m0]
        //     \
        //      [f1] - [f2] - [f3] - [f4] <- fork tip
        //
        //
        // Or in a situation where it is shorter:
        //
        //      [m1] - [m2] - [m3] <- main tip
        //     /
        // [m0]
        //     \
        //      [f1] - [f2] - [f3] - [f4] <- fork tip

        let fork_tip_hash = fork_tip_header.block_hash.clone();
        let mut fork_header_cursor = fork_tip_header;

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

        // Updating tip of the new main chain
        self.mainchain_tip_blockhash = fork_tip_hash;
```
