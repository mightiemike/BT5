The code is clear. Let me trace the exact bug path before writing the final answer.

**Key facts from the code:**

1. `get_header_by_height` (lib.rs:677-682) reads exclusively from `mainchain_height_to_header` — the height→hash map that only contains mainchain blocks.

2. `get_next_work_required` (bitcoin.rs:78-86) calls `blocks_getter.get_header_by_height(first_block_height)` to fetch the interval-start block for retarget calculation.

3. `get_prev_header` (lib.rs:671-675) correctly follows fork lineage via `headers_pool` keyed by `prev_block_hash`.

4. During fork block validation, `check_pow` → `get_next_work_required` is called with `self` as the `BlocksGetter`, so `get_header_by_height` returns the **mainchain** block at `first_block_height`, not the fork's ancestor.

---

### Title
Fork Retarget Uses Mainchain Ancestor Instead of Fork Ancestor — (`contract/src/bitcoin.rs` + `contract/src/lib.rs`)

### Summary

`get_header_by_height` always reads from `mainchain_height_to_header`. When `get_next_work_required` calls it during fork block validation at a retarget boundary, it fetches the mainchain block at `first_block_height` instead of the fork's own ancestor at that height. An attacker who publishes a crafted testnet fork (relayed by the trusted relayer) can exploit this to get the contract to accept a retarget block whose `bits` field is computed against the wrong timestamp, diverging from what the real Bitcoin network would require.

### Finding Description

In `get_next_work_required`:

```rust
let first_block_height =
    prev_block_header.block_height - (config.difficulty_adjustment_interval - 1);

let interval_tail_extend_header = blocks_getter.get_header_by_height(first_block_height);
``` [1](#0-0) 

`blocks_getter` is `BtcLightClient`, whose `get_header_by_height` implementation is:

```rust
fn get_header_by_height(&self, height: u64) -> ExtendedHeader {
    self.mainchain_height_to_header
        .get(&height)
        .and_then(|hash| self.headers_pool.get(&hash))
        .unwrap_or_else(|| env::panic_str(ERR_KEY_NOT_EXIST))
}
``` [2](#0-1) 

This is a height-indexed lookup into the **mainchain** map only. There is no fork-aware path. By contrast, `get_prev_header` correctly follows fork lineage via hash-based lookup in `headers_pool`: [3](#0-2) 

The asymmetry is structural: backward traversal (MTP, min-difficulty walk) is fork-correct; the retarget interval-start lookup is not.

**Concrete exploit path:**

Let the contract store mainchain blocks at heights `N` through `N+2015` (where `N % 2016 == 0`). The attacker publishes a testnet fork that diverges at height `N`, with a fork block at height `N+1` carrying timestamp `T_fork > T_main` (later than the mainchain's block at `N+1`). The fork builds to height `N+2015`.

When the relayer submits the fork block at height `N+2016` (the retarget block):

- `first_block_height = N+2016 - 2015 = N+1`
- `get_header_by_height(N+1)` returns the **mainchain** block with timestamp `T_main`
- `calculate_next_work_required` computes `actual_time = T_end - T_main` (a longer interval → easier difficulty)
- The correct computation would use `T_fork > T_main`, giving `actual_time = T_end - T_fork` (shorter interval → harder difficulty, capped at 4×)
- The contract accepts a retarget block with `bits` set to the easier (incorrect) value [4](#0-3) 

After the retarget block is accepted, subsequent fork blocks within the new interval inherit the incorrect `bits` via `prev_block_header.block_header.bits`: [5](#0-4) 

The fork can then accumulate chainwork and trigger a reorg via `submit_block_header_inner`: [6](#0-5) 

### Impact Explanation

The contract stores a fork whose retarget block carries a `bits` value the real Bitcoin network would reject. After a reorg, `mainchain_height_to_header` and `mainchain_tip_blockhash` point to this invalid fork. Any downstream bridge calling `verify_transaction_inclusion` or `verify_transaction_inclusion_v2` against blocks on this fork will treat confirmations as final that the real Bitcoin network does not recognize — enabling double-spend or fund theft. [7](#0-6) 

### Likelihood Explanation

On Bitcoin testnet, `pow_allow_min_difficulty_blocks = true` and difficulty is low. An attacker can mine a 2016-block fork with manipulated timestamps at modest cost. The trusted relayer will relay whichever chain has the most chainwork; if the attacker's fork achieves that, the relayer submits it. The attacker does not need any privileged role — only the ability to publish blocks on the Bitcoin testnet P2P network. [8](#0-7) 

### Recommendation

Replace the height-based lookup in `get_next_work_required` with a backward walk along the fork's own lineage using `get_prev_header`. Starting from `prev_block_header`, walk back `difficulty_adjustment_interval - 1` steps via `prev_block_hash` links (all of which are in `headers_pool` for any validly submitted fork). This mirrors Bitcoin Core's `GetAncestor` approach and ensures the retarget always uses the fork's own interval-start block, not whatever block currently occupies that height on the mainchain.

### Proof of Concept

1. Initialize the contract on testnet with genesis at height `N` (where `N % 2016 == 0`).
2. Submit mainchain blocks `N+1` … `N+2015` with timestamps `T_main[i]`.
3. Construct a fork diverging at `N`: fork block at `N+1` has `T_fork > T_main[1]` (e.g., `T_main[1] + 1000`). Build fork blocks `N+2` … `N+2015` with valid PoW.
4. Compute the correct retarget bits using `T_fork` as `first_block_time` → call this `bits_correct` (harder).
5. Compute the contract's expected bits using `T_main[1]` as `first_block_time` → call this `bits_contract` (easier, higher value).
6. Mine fork block at `N+2016` with `bits = bits_contract` and valid PoW against that easier target.
7. Submit the fork via the relayer. The contract's `check_pow` calls `get_next_work_required`, which calls `get_header_by_height(N+1)` and returns the mainchain block (timestamp `T_main[1]`), computing `bits_contract`. The check passes.
8. Continue mining fork blocks with `bits_contract`. Once the fork's chainwork exceeds the mainchain's, `submit_block_header_inner` triggers `reorg_chain`, and the contract's canonical chain is now the attacker's fork.
9. A bridge call to `verify_transaction_inclusion` against a transaction on the fork returns `true` for a transaction that does not exist on the real Bitcoin testnet canonical chain. [9](#0-8)

### Citations

**File:** contract/src/bitcoin.rs (L19-46)
```rust
    pub(crate) fn check_pow(&self, block_header: &Header, prev_block_header: &ExtendedHeader) {
        let config = self.get_config();
        let expected_bits = get_next_work_required(&config, block_header, prev_block_header, self);

        require!(
            expected_bits == block_header.bits,
            "bad-diffbits: incorrect proof of work"
        );

        // Check timestamp against prev
        require!(
            block_header.time > get_median_time_past(prev_block_header.clone(), self),
            "time-too-old: block's timestamp is too early"
        );

        // Check timestamp
        let current_timestamp = u32::try_from(env::block_timestamp_ms() / 1000).unwrap(); // Convert to seconds
        require!(
            block_header.time <= current_timestamp + MAX_FUTURE_BLOCK_TIME_LOCAL,
            "time-too-new: block timestamp too far in the future"
        );

        // Reject blocks with outdated version
        require!(
            block_header.version >= 4,
            "bad-version: block version must be at least 4"
        );
    }
```

**File:** contract/src/bitcoin.rs (L56-76)
```rust
    if (prev_block_header.block_height + 1) % config.difficulty_adjustment_interval != 0 {
        if config.pow_allow_min_difficulty_blocks {
            if block_header.time
                > prev_block_header.block_header.time + 2 * config.pow_target_spacing
            {
                return config.proof_of_work_limit_bits;
            }

            let mut current_block_header = prev_block_header.clone();
            while current_block_header.block_header.bits == config.proof_of_work_limit_bits
                && current_block_header.block_height % config.difficulty_adjustment_interval != 0
            {
                current_block_header =
                    blocks_getter.get_prev_header(&current_block_header.block_header);
            }

            let last_bits = current_block_header.block_header.bits;
            return last_bits;
        }
        return prev_block_header.block_header.bits;
    }
```

**File:** contract/src/bitcoin.rs (L78-86)
```rust
    let first_block_height =
        prev_block_header.block_height - (config.difficulty_adjustment_interval - 1);

    let interval_tail_extend_header = blocks_getter.get_header_by_height(first_block_height);
    calculate_next_work_required(
        config,
        prev_block_header,
        interval_tail_extend_header.block_header.time.into(),
    )
```

**File:** contract/src/bitcoin.rs (L90-117)
```rust
fn calculate_next_work_required(
    config: &NetworkConfig,
    prev_block_header: &ExtendedHeader,
    first_block_time: i64,
) -> u32 {
    let prev_block_time: i64 = prev_block_header.block_header.time.into();

    let mut actual_time_taken: i64 = prev_block_time - first_block_time;
    if actual_time_taken < config.pow_target_timespan / 4 {
        actual_time_taken = config.pow_target_timespan / 4;
    }
    if actual_time_taken > config.pow_target_timespan * 4 {
        actual_time_taken = config.pow_target_timespan * 4;
    }

    let new_target = target_from_bits(prev_block_header.block_header.bits);

    let (mut new_target, new_target_overflow) =
        new_target.overflowing_mul(<i64 as TryInto<u64>>::try_into(actual_time_taken).unwrap());
    require!(!new_target_overflow, "new target overflow");
    new_target = new_target
        / U256::from(<i64 as TryInto<u64>>::try_into(config.pow_target_timespan).unwrap());

    if new_target > config.pow_limit {
        new_target = config.pow_limit;
    }

    new_target.target_to_bits()
```

**File:** contract/src/lib.rs (L120-124)
```rust
#[trusted_relayer(
    bypass_roles(Role::DAO, Role::UnrestrictedSubmitBlocks),
    manager_roles(Role::DAO, Role::RelayerManager),
    config_roles(Role::DAO)
)]
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

**File:** contract/src/lib.rs (L562-567)
```rust
            // Current chainwork is higher than on a current mainchain, let's promote the fork
            if current_header.chain_work > total_main_chain_chainwork {
                log!("Chain reorg");
                self.reorg_chain(current_header, last_main_chain_block_height);
            }
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
