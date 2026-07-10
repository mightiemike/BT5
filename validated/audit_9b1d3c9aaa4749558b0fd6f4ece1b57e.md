### Title
Dogecoin Fork Difficulty Check Uses Mainchain Ancestor Timestamp Instead of Fork Ancestor Timestamp - (File: `contract/src/dogecoin.rs`)

---

### Summary

In the Dogecoin build of the BTC light client, `get_next_work_required` fetches the "first block" of the difficulty adjustment interval via `get_header_by_height`, which always resolves against the **mainchain** height-to-hash map. When the block being validated belongs to a fork, the mainchain block at that height is a different block than the fork's actual ancestor. The resulting `first_block_time` is therefore wrong, and the computed required difficulty diverges from the correct value. An unprivileged relayer can exploit this by crafting a fork whose divergence-point block carries a timestamp higher than the corresponding mainchain block, causing the contract to accept subsequent fork blocks at a lower difficulty than the protocol requires.

---

### Finding Description

`get_next_work_required` in `contract/src/dogecoin.rs` computes the difficulty for the next block using a `modulated_timespan` derived from two timestamps: the previous block's time and the time of the block at `height_first`. [1](#0-0) 

`height_first` is resolved through `blocks_getter.get_header_by_height`, which is implemented as: [2](#0-1) 

This lookup is keyed exclusively on `mainchain_height_to_header`. Fork blocks are stored only in `headers_pool` (via `store_fork_header`), never in `mainchain_height_to_header`: [3](#0-2) 

The developer already flagged this as uncertain with an inline TODO: [4](#0-3) 

For Dogecoin mainnet, `difficulty_adjustment_interval = 1` for all blocks at height ≥ 145,000: [5](#0-4) 

With `difficulty_adjustment_interval = 1`, `blocks_to_go_back = 1` for all non-genesis blocks, so `height_first = prev_block_header.block_height - 1`. This means the difficulty check for a fork block at height H+2 uses the **mainchain** block at height H as `first_block_time`, not the fork's block at height H.

The Digishield adjustment formula is: [6](#0-5) 

If the mainchain block at H has timestamp `T_main_H` and the fork's block at H has timestamp `T_fork_H`, and the attacker sets `T_fork_H > T_main_H` (within MTP constraints), then the contract computes `modulated_timespan` using `T_main_H` (smaller), producing a larger timespan than the correct value. A larger timespan yields a higher (easier) target, so the contract accepts fork blocks at lower difficulty than the protocol requires.

The call chain from the public entry point is:

`submit_blocks` → `submit_block_header` (dogecoin) → `check_target` → `check_pow` → `get_next_work_required` → `get_header_by_height` (mainchain-only lookup) [7](#0-6) 

---

### Impact Explanation

The broken invariant is: *the difficulty of every submitted block must be validated against the correct ancestor's timestamps*. When a fork block at H+2 is validated using the mainchain block at H instead of the fork's block at H, the required `bits` value computed by the contract is lower than the protocol-correct value. The contract then enforces this weaker target via: [8](#0-7) 

A fork built with under-difficulty blocks accumulates less real chainwork per block than a legitimately mined chain. If the fork's cumulative `chain_work` still exceeds the mainchain tip's `chain_work` (because the attacker mines enough such blocks), `reorg_chain` is triggered and the contract's canonical chain is replaced with the attacker's fork. Downstream consumers calling `verify_transaction_inclusion` or `verify_transaction_inclusion_v2` then verify proofs against a chain that was accepted with insufficient proof-of-work, corrupting the SPV guarantee the contract provides. [9](#0-8) 

**Impact: High** — the canonical chain mapping and all SPV proof results derived from it are corrupted.

---

### Likelihood Explanation

`submit_blocks` is a payable, unprivileged call gated only by the `trusted_relayer` macro and a storage deposit. Any registered relayer (or an account that bypasses the relayer check via `Role::UnrestrictedSubmitBlocks`) can submit arbitrary fork headers. The attacker needs only to:

1. Identify the current mainchain tip height H on the Dogecoin deployment.
2. Construct a fork block at H whose timestamp is higher than the mainchain block at H (trivially achievable within the 2-hour future-time window).
3. Build subsequent fork blocks at H+1, H+2, … with the under-difficulty `bits` value the contract will accept.
4. Submit the batch via `submit_blocks`.

No privileged role, leaked key, or social engineering is required. The Dogecoin mainnet config has `difficulty_adjustment_interval = 1`, so the desynchronization manifests at the very third fork block (H+2), making the attack immediately practical.

**Likelihood: High**

---

### Recommendation

Replace the `get_header_by_height` call in `get_next_work_required` (Dogecoin) with an ancestor walk using `get_prev_header`, which follows the actual chain by hash and is therefore correct for both mainchain and fork blocks. The Zcash implementation already does this correctly: [10](#0-9) 

The Dogecoin function should walk back `blocks_to_go_back` steps from `prev_block_header` using `get_prev_header` to obtain the true ancestor's timestamp, rather than looking up the mainchain block at `height_first`.

---

### Proof of Concept

**Setup**: Dogecoin mainnet deployment, current mainchain tip at height H (H ≥ 145,001).

**State before attack**:
- Mainchain block at H: timestamp `T_main_H`, bits `B_main`.
- `mainchain_height_to_header[H]` → mainchain block hash.

**Attack steps**:

1. Craft fork block `F_H` with `prev_block_hash` = mainchain block at H−1, timestamp `T_fork_H = T_main_H + Δ` (Δ up to 7200 s), bits = `B_main` (valid PoW against `B_main`).
2. Submit `F_H` via `submit_blocks`. It is stored as a fork block in `headers_pool` only.
3. Craft fork block `F_{H+1}` with `prev_block_hash` = hash of `F_H`. Its difficulty check uses `get_header_by_height(H−1)` = mainchain block at H−1 = fork's actual ancestor at H−1 (same block, no error yet).
4. Craft fork block `F_{H+2}` with `prev_block_hash` = hash of `F_{H+1}`. Its difficulty check calls `get_header_by_height(H)` → returns **mainchain block at H** with timestamp `T_main_H`, not `F_H` with timestamp `T_main_H + Δ`.
5. The contract computes `modulated_timespan` using `T_fork_{H+1} − T_main_H` instead of `T_fork_{H+1} − T_fork_H`. Since `T_main_H < T_fork_H`, the computed timespan is larger by Δ, and the Digishield formula yields a higher (easier) target.
6. The contract enforces this weaker `bits` value. `F_{H+2}` is accepted with lower difficulty than the protocol requires.
7. Repeat for `F_{H+3}`, `F_{H+4}`, … Each subsequent fork block benefits from the same desynchronization (mainchain block at H+k vs. fork block at H+k).
8. Once the fork's cumulative `chain_work` exceeds the mainchain's, `reorg_chain` fires, replacing the canonical chain with the attacker's under-difficulty fork. [11](#0-10) [12](#0-11)

### Citations

**File:** contract/src/dogecoin.rs (L27-33)
```rust
        require!(
            expected_bits == block_header.bits,
            format!(
                "Error: Incorrect target. Expected bits: {:?}, Actual bits: {:?}",
                expected_bits, block_header.bits
            )
        );
```

**File:** contract/src/dogecoin.rs (L166-204)
```rust
    pub(crate) fn submit_block_header(
        &mut self,
        header: (Header, Option<AuxData>),
        skip_pow_verification: bool,
    ) {
        let (block_header, aux_data) = header;

        let prev_block_header = self.get_prev_header(&block_header);
        let current_block_hash = block_header.block_hash();

        if !skip_pow_verification {
            self.check_target(&block_header, &prev_block_header);

            if let Some(ref aux_data) = aux_data {
                self.check_aux(&block_header, aux_data);
            } else {
                let pow_hash = block_header.block_hash_pow();
                // Check if the block hash is less than or equal to the target
                require!(
                    U256::from_le_bytes(&pow_hash.0) <= target_from_bits(block_header.bits),
                    format!("block should have correct pow")
                );
            }
        }

        let (current_block_computed_chain_work, overflow) = prev_block_header
            .chain_work
            .overflowing_add(work_from_bits(block_header.bits));
        require!(!overflow, "Addition of U256 values overflowed");

        let current_header = ExtendedHeader {
            block_header: block_header.clone().into_light(),
            block_hash: current_block_hash,
            chain_work: current_block_computed_chain_work,
            block_height: 1 + prev_block_header.block_height,
        };

        self.submit_block_header_inner(current_header, &prev_block_header);
    }
```

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

**File:** contract/src/dogecoin.rs (L307-318)
```rust
    let modulated_timespan = i64::from(prev_block_header.block_header.time) - first_block_time;

    let mut modulated_timespan = retarget_timespan + (modulated_timespan - retarget_timespan) / 8;

    let min_timespan = retarget_timespan - (retarget_timespan / 4);
    let max_timespan = retarget_timespan + (retarget_timespan / 2);

    if modulated_timespan < min_timespan {
        modulated_timespan = min_timespan;
    } else if modulated_timespan > max_timespan {
        modulated_timespan = max_timespan;
    }
```

**File:** contract/src/lib.rs (L562-566)
```rust
            // Current chainwork is higher than on a current mainchain, let's promote the fork
            if current_header.chain_work > total_main_chain_chainwork {
                log!("Chain reorg");
                self.reorg_chain(current_header, last_main_chain_block_height);
            }
```

**File:** contract/src/lib.rs (L664-667)
```rust
    /// Stores and handles fork submissions
    fn store_fork_header(&mut self, header: &ExtendedHeader) {
        self.headers_pool.insert(&header.block_hash, header);
    }
```

**File:** contract/src/lib.rs (L670-682)
```rust
impl BlocksGetter for BtcLightClient {
    fn get_prev_header(&self, current_header: &LightHeader) -> ExtendedHeader {
        self.headers_pool
            .get(&current_header.prev_block_hash)
            .unwrap_or_else(|| env::panic_str("PrevBlockNotFound"))
    }

    fn get_header_by_height(&self, height: u64) -> ExtendedHeader {
        self.mainchain_height_to_header
            .get(&height)
            .and_then(|hash| self.headers_pool.get(&hash))
            .unwrap_or_else(|| env::panic_str(ERR_KEY_NOT_EXIST))
    }
```

**File:** contract/src/zcash.rs (L87-103)
```rust
    let mut current_header = prev_block_header.clone();
    let mut total_target = U256::ZERO;
    let mut median_time = [0u32; MEDIAN_TIME_SPAN];

    let prev_block_median_time_past = {
        for i in 0..usize::try_from(config.pow_averaging_window).unwrap() {
            if i < MEDIAN_TIME_SPAN {
                median_time[i] = current_header.block_header.time;
            }

            let (sum, overflow) =
                total_target.overflowing_add(target_from_bits(current_header.block_header.bits));
            require!(!overflow, "Addition of U256 values overflowed");
            total_target = sum;

            current_header = prev_block_getter.get_prev_header(&current_header.block_header);
        }
```
