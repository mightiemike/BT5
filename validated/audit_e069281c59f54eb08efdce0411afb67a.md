### Title
Fork Block Difficulty Calculated Against Stale Mainchain Ancestor Instead of Fork Ancestor — (`contract/src/dogecoin.rs`, `contract/src/bitcoin.rs`)

---

### Summary

When validating a fork block's proof-of-work difficulty, `get_next_work_required` fetches the retarget-interval boundary block via `get_header_by_height`, which resolves exclusively against the **mainchain** height-to-hash mapping. If the fork diverges before that boundary height, the mainchain block at that height is a different block from the fork's actual ancestor, carrying a different timestamp. The difficulty is therefore computed from a stale, wrong timestamp, breaking the PoW validation invariant for fork submissions.

---

### Finding Description

In `get_next_work_required` (Dogecoin variant), after computing `height_first`, the boundary block is fetched as:

```rust
// TODO: check if it is correct to get block header by height from mainchain without looping to find the ancestor
let first_block_time = blocks_getter
    .get_header_by_height(height_first)
    .block_header
    .time;
``` [1](#0-0) 

`get_header_by_height` is implemented as:

```rust
fn get_header_by_height(&self, height: u64) -> ExtendedHeader {
    self.mainchain_height_to_header
        .get(&height)
        .and_then(|hash| self.headers_pool.get(&hash))
        .unwrap_or_else(|| env::panic_str(ERR_KEY_NOT_EXIST))
}
``` [2](#0-1) 

`mainchain_height_to_header` is a map that is **only updated for mainchain blocks**. Fork blocks are stored in `headers_pool` but are never inserted into `mainchain_height_to_header`. [3](#0-2) 

The call sequence for a fork block submission is:

1. `submit_block_header` fetches `prev_block_header` from `headers_pool` (correctly resolves the fork parent).
2. `check_target` → `check_pow` → `get_next_work_required` is called **before** any reorg occurs.
3. Inside `get_next_work_required`, `height_first` is computed as `prev_block_header.block_height - blocks_to_go_back`.
4. `get_header_by_height(height_first)` returns the **mainchain** block at that height, not the fork's ancestor. [4](#0-3) 

For Dogecoin after block 145,000, `difficulty_adjustment_interval = 1`, so **every block is a retarget** and `blocks_to_go_back = 1`. This means for any fork block at height `H+2`, the boundary block is at `H`, and if the fork diverged at `H`, the mainchain block at `H` and the fork block at `H` are distinct blocks with potentially different timestamps. [5](#0-4) 

The same structural flaw exists in the Bitcoin variant:

```rust
let interval_tail_extend_header = blocks_getter.get_header_by_height(first_block_height);
``` [6](#0-5) 

The developers themselves flagged this with a `TODO` comment in the Dogecoin path, confirming awareness of the ambiguity. [7](#0-6) 

---

### Impact Explanation

The difficulty calculation in `calculate_next_work_required` uses `first_block_time` as the start of the measured timespan:

```rust
let modulated_timespan = i64::from(prev_block_header.block_header.time) - first_block_time;
``` [8](#0-7) 

If the mainchain block at `height_first` has an **earlier** timestamp than the fork's actual ancestor at the same height, the computed timespan is artificially inflated, producing a **lower required difficulty** (easier target). The contract then accepts a fork block whose actual PoW does not meet the difficulty that the Bitcoin/Dogecoin protocol would require for that fork chain. This corrupts the PoW verification invariant: the contract certifies a block as valid when it would be rejected by a full node following the fork chain's own history.

A downstream NEAR dApp calling `verify_transaction_inclusion` or `verify_transaction_inclusion_v2` against a block that was accepted via this weakened difficulty check receives a `true` result for a block that is not legitimately part of any valid chain. [9](#0-8) 

---

### Likelihood Explanation

For Dogecoin (post-block 145,000), the per-block retarget means the boundary block is always just one block behind the fork parent. Any fork that diverges at height `H` immediately creates a discrepancy at `H` for the very next fork block submitted at `H+2`. An unprivileged relayer-path user can submit fork blocks via `submit_blocks`; no privileged role is required. The attacker only needs to control the timestamps of their fork blocks (which are part of the submitted header data) to engineer a favorable discrepancy relative to the mainchain block at the same height. [10](#0-9) 

---

### Recommendation

Replace the `get_header_by_height` lookup with an ancestor traversal that walks the `prev_block_hash` chain from `prev_block_header` backward by `blocks_to_go_back` steps. This ensures the boundary block is always the fork's own ancestor, not the mainchain block at the same height. The `get_prev_header` helper already exists and is used correctly elsewhere for this purpose. [11](#0-10) 

---

### Proof of Concept

1. The mainchain contains blocks at heights `H`, `H+1`, `H+2`, … with timestamps `T_m[H]`, `T_m[H+1]`, …
2. An attacker submits a fork block at height `H` (diverging from the mainchain at `H-1`) with timestamp `T_f[H]` where `T_f[H] > T_m[H]` (fork block is newer than the mainchain block at the same height).
3. The attacker submits a fork block at height `H+1` with timestamp `T_f[H+1]`.
4. When the attacker submits the fork block at height `H+2`, `check_pow` calls `get_next_work_required`:
   - `prev_block_header` = fork block at `H+1` ✓
   - `height_first = H+1 - 1 = H`
   - `get_header_by_height(H)` returns the **mainchain** block at `H` with timestamp `T_m[H]`
   - `modulated_timespan = T_f[H+1] - T_m[H]` (uses mainchain timestamp, not fork timestamp)
   - Since `T_m[H] < T_f[H]`, the timespan is inflated → required difficulty is lower than it should be
5. The contract accepts the fork block at `H+2` with less PoW than the Dogecoin protocol requires for that fork chain.
6. If the fork accumulates enough chainwork (even with reduced per-block work), `reorg_chain` promotes it to the mainchain, and subsequent `verify_transaction_inclusion` calls against its blocks return `true`. [12](#0-11)

### Citations

**File:** contract/src/dogecoin.rs (L229-297)
```rust
fn get_next_work_required(
    config: &DogecoinConfig,
    block_header: &Header,
    prev_block_header: &ExtendedHeader,
    blocks_getter: &impl BlocksGetter,
) -> u32 {
    // Dogecoin: Special rules for minimum difficulty blocks with Digishield
    if allow_min_difficulty_for_block(config, block_header, prev_block_header) {
        // Special difficulty rule for testnet:
        // If the new block's timestamp is more than 2* nTargetSpacing minutes
        // then allow mining of a min-difficulty block.
        return config.proof_of_work_limit_bits;
    }

    // Only change once per difficulty adjustment interval
    let new_difficulty_protocol = prev_block_header.block_height >= 145_000;
    let difficulty_adjustment_interval = if new_difficulty_protocol {
        1
    } else {
        config.difficulty_adjustment_interval
    };

    if (prev_block_header.block_height + 1) % difficulty_adjustment_interval != 0 {
        if config.pow_allow_min_difficulty_blocks {
            // Special difficulty rule for testnet:
            // If the new block's timestamp is more than 2* 10 minutes
            // then allow mining of a min-difficulty block.
            if block_header.time
                > prev_block_header.block_header.time + config.pow_target_spacing * 2
            {
                return config.proof_of_work_limit_bits;
            }

            // Return the last non-special-min-difficulty-rules-block
            let mut current_block_header = prev_block_header.clone();

            while current_block_header.block_header.bits == config.proof_of_work_limit_bits
                && current_block_header.block_height % config.difficulty_adjustment_interval != 0
            {
                current_block_header =
                    blocks_getter.get_prev_header(&current_block_header.block_header);
            }

            return current_block_header.block_header.bits;
        }

        return prev_block_header.block_header.bits;
    }

    // Litecoin: This fixes an issue where a 51% attack can change difficulty at will.
    // Go back the full period unless it's the first retarget after genesis. Code courtesy of Art Forz
    let mut blocks_to_go_back = difficulty_adjustment_interval - 1;
    if prev_block_header.block_height + 1 != difficulty_adjustment_interval {
        blocks_to_go_back = difficulty_adjustment_interval;
    }

    // Go back by what we want to be 14 days worth of blocks
    let height_first = prev_block_header
        .block_height
        .checked_sub(blocks_to_go_back)
        .unwrap_or_else(|| env::panic_str("Height underflow when calculating first block height"));

    // TODO: check if it is correct to get block header by height from mainchain without looping to find the ancestor
    let first_block_time = blocks_getter
        .get_header_by_height(height_first)
        .block_header
        .time;

    calculate_next_work_required(config, prev_block_header, i64::from(first_block_time))
```

**File:** contract/src/dogecoin.rs (L307-309)
```rust
    let modulated_timespan = i64::from(prev_block_header.block_header.time) - first_block_time;

    let mut modulated_timespan = retarget_timespan + (modulated_timespan - retarget_timespan) / 8;
```

**File:** contract/src/lib.rs (L169-198)
```rust
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

**File:** contract/src/lib.rs (L288-323)
```rust
    pub fn verify_transaction_inclusion(&self, #[serializer(borsh)] args: ProofArgs) -> bool {
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

        require!(!args.merkle_proof.is_empty(), "Merkle proof is empty");

        // compute merkle tree root and check if it matches block's original merkle tree root
        merkle_tools::compute_root_from_merkle_proof(
            args.tx_id,
            usize::try_from(args.tx_index).unwrap(),
            &args.merkle_proof,
        ) == header.block_header.merkle_root
    }
```

**File:** contract/src/lib.rs (L650-656)
```rust
    fn store_block_header(&mut self, header: &ExtendedHeader) {
        self.mainchain_height_to_header
            .insert(&header.block_height, &header.block_hash);
        self.mainchain_header_to_height
            .insert(&header.block_hash, &header.block_height);
        self.headers_pool.insert(&header.block_hash, header);
    }
```

**File:** contract/src/lib.rs (L671-675)
```rust
    fn get_prev_header(&self, current_header: &LightHeader) -> ExtendedHeader {
        self.headers_pool
            .get(&current_header.prev_block_hash)
            .unwrap_or_else(|| env::panic_str("PrevBlockNotFound"))
    }
```

**File:** contract/src/lib.rs (L677-682)
```rust
    fn get_header_by_height(&self, height: u64) -> ExtendedHeader {
        self.mainchain_height_to_header
            .get(&height)
            .and_then(|hash| self.headers_pool.get(&hash))
            .unwrap_or_else(|| env::panic_str(ERR_KEY_NOT_EXIST))
    }
```

**File:** contract/src/bitcoin.rs (L81-86)
```rust
    let interval_tail_extend_header = blocks_getter.get_header_by_height(first_block_height);
    calculate_next_work_required(
        config,
        prev_block_header,
        interval_tail_extend_header.block_header.time.into(),
    )
```
