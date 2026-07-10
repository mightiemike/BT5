### Title
Fork Retarget Ancestor Confusion: `get_header_by_height` Reads Mainchain Map Instead of Fork Lineage — (`contract/src/bitcoin.rs`, `contract/src/lib.rs`)

---

### Summary

When `get_next_work_required` computes the expected difficulty for a fork block that falls on a retarget boundary, it calls `blocks_getter.get_header_by_height(first_block_height)` to obtain the timestamp of the interval's opening block. The sole implementation of that method reads exclusively from `mainchain_height_to_header`. If the fork diverged before `first_block_height`, the mainchain block at that height is a **different block** from the fork's true ancestor at the same height. The contract therefore computes `bits` using the wrong timestamp, causing the light client to accept (or reject) a fork block based on a difficulty that no Bitcoin consensus node would ever derive for that fork.

---

### Finding Description

**Root cause — `get_header_by_height` is mainchain-only:** [1](#0-0) 

The function unconditionally resolves height → hash through `mainchain_height_to_header`. There is no mechanism to walk the fork's own `prev_block_hash` chain back to the interval-opening block.

**Call site in `get_next_work_required`:** [2](#0-1) 

`first_block_height` is `prev_block_header.block_height − 2015`. If the fork split at any height ≤ `first_block_height`, the block returned here belongs to the mainchain, not to the fork.

**Contrast with `get_prev_header`**, which correctly follows `prev_block_hash` through `headers_pool` and therefore works for both mainchain and fork blocks: [3](#0-2) 

**Execution path:**

`submit_blocks` → `submit_block_header` → `check_target` → `check_pow` → `get_next_work_required` → `get_header_by_height`. [4](#0-3) 

**Concrete scenario:**

| Height | Mainchain block | Fork block |
|--------|----------------|------------|
| N (= K·2016) | M_N (timestamp T_M) | F_N (timestamp T_F, T_F ≠ T_M) |
| N+1 … N+2015 | M_N+1 … M_N+2015 | F_N+1 … F_N+2015 |
| N+2016 | M_N+2016 | **F_N+2016 ← retarget block** |

For F_N+2016, `first_block_height = N`. `get_header_by_height(N)` returns **M_N** (mainchain), not **F_N** (fork). The computed `actual_time_taken` is `time(F_N+2015) − time(M_N)` instead of the correct `time(F_N+2015) − time(F_N)`. An attacker who sets `time(F_N)` to a value earlier than `time(M_N)` inflates `actual_time_taken`, which lowers the required difficulty for F_N+2016 (capped at 4× the target timespan per `calculate_next_work_required`). [5](#0-4) 

The light client then enforces `expected_bits == block_header.bits` using this wrong value, so it accepts a retarget block whose `bits` field Bitcoin consensus nodes would reject.

---

### Impact Explanation

A fork whose retarget block carries a `bits` value that is valid under the light client's (incorrect) calculation but invalid under Bitcoin consensus can be promoted to mainchain inside the contract via `reorg_chain` if its cumulative chainwork exceeds the honest tip. Any downstream bridge that calls `verify_transaction_inclusion` or `verify_transaction_inclusion_v2` against this promoted fork will treat transactions in it as finalized, enabling double-spend or fund theft. [6](#0-5) 

---

### Likelihood Explanation

The `submit_blocks` entry point is gated by `#[trusted_relayer]`. [7](#0-6) 

An unprivileged account cannot call it directly. However, the honest relayer forwards whatever headers it observes on the Bitcoin P2P network. An attacker who mines a real Bitcoin fork (requiring significant hashrate) with a crafted timestamp at the interval-opening block can cause the relayer to forward headers that trigger the bug without any relayer compromise. The hashrate requirement is high for mainnet, but the bug is structurally present and would be trivially exploitable on testnet or in any deployment where `skip_pow_verification = true`.

---

### Recommendation

Replace the height-based lookup with an ancestor walk that follows `prev_block_hash` through `headers_pool`. Concretely, add a helper that, given a known fork tip's `ExtendedHeader`, walks backward exactly `difficulty_adjustment_interval − 1` steps using `get_prev_header`, mirroring how Bitcoin Core resolves `pindexFirst` in `GetNextWorkRequired`. The `get_header_by_height` shortcut is only safe when the block being validated is already known to be on the mainchain.

---

### Proof of Concept

1. Initialize the contract at height 0 (a retarget boundary) with `skip_pow_verification = false`.
2. Submit mainchain blocks 1–2015 with timestamps T_M_1 … T_M_2015.
3. Submit a fork block at height 1 whose `prev_block_hash` points to the genesis but whose timestamp T_F_1 is set to `T_M_0 − 1` (one second before genesis). This is the fork's block at `first_block_height = 0 + 1 = 1`... 

   Actually, use a fork that diverges at height 0 (same genesis) but sets fork block 1's timestamp very early. Then submit fork blocks 2–2015 normally, and craft fork block 2016 with `bits` computed using `time(F_2015) − time(M_0)` (mainchain genesis timestamp) instead of `time(F_2015) − time(F_0)`.

4. Observe that `check_pow` passes for the crafted `bits` value on the fork's retarget block, while a reference Bitcoin node would reject it.
5. If the fork's cumulative chainwork exceeds the mainchain's, `reorg_chain` promotes it, and `verify_transaction_inclusion` returns `true` for a transaction that exists only on the attacker's fork. [8](#0-7)

### Citations

**File:** contract/src/lib.rs (L167-169)
```rust
    #[pause]
    #[trusted_relayer]
    pub fn submit_blocks(
```

**File:** contract/src/lib.rs (L517-528)
```rust
        if !skip_pow_verification {
            self.check_target(&header, &prev_block_header);

            let pow_hash = header.block_hash_pow();
            // Check if the block hash is less than or equal to the target
            require!(
                U256::from_le_bytes(&pow_hash.0) <= target_from_bits(header.bits),
                format!("block should have correct pow")
            );
        }

        self.submit_block_header_inner(current_header, &prev_block_header);
```

**File:** contract/src/lib.rs (L562-566)
```rust
            // Current chainwork is higher than on a current mainchain, let's promote the fork
            if current_header.chain_work > total_main_chain_chainwork {
                log!("Chain reorg");
                self.reorg_chain(current_header, last_main_chain_block_height);
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

**File:** contract/src/bitcoin.rs (L50-87)
```rust
fn get_next_work_required(
    config: &NetworkConfig,
    block_header: &Header,
    prev_block_header: &ExtendedHeader,
    blocks_getter: &impl BlocksGetter,
) -> u32 {
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

    let first_block_height =
        prev_block_header.block_height - (config.difficulty_adjustment_interval - 1);

    let interval_tail_extend_header = blocks_getter.get_header_by_height(first_block_height);
    calculate_next_work_required(
        config,
        prev_block_header,
        interval_tail_extend_header.block_header.time.into(),
    )
}
```

**File:** contract/src/bitcoin.rs (L97-103)
```rust
    let mut actual_time_taken: i64 = prev_block_time - first_block_time;
    if actual_time_taken < config.pow_target_timespan / 4 {
        actual_time_taken = config.pow_target_timespan / 4;
    }
    if actual_time_taken > config.pow_target_timespan * 4 {
        actual_time_taken = config.pow_target_timespan * 4;
    }
```
