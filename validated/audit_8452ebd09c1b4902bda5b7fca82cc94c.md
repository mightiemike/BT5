Based on my thorough analysis of the code, here is my determination:

---

### Title
Fork Retarget Ancestor Confusion: `get_header_by_height` Reads Mainchain Block Instead of Fork Ancestor During Difficulty Adjustment — (File: `contract/src/lib.rs` + `contract/src/litecoin.rs`)

### Summary

`get_header_by_height` unconditionally reads from `mainchain_height_to_header`. When `get_next_work_required` calls it to find the interval-start block for a fork candidate at a retarget boundary, it silently returns the **mainchain** block at that height instead of the fork's true ancestor. An attacker who controls a Litecoin-testnet fork can exploit this to make the contract accept a retarget block whose `bits` field the source chain would reject, and a downstream bridge would then treat those confirmations as final.

---

### Finding Description

**Root cause — `get_header_by_height` is mainchain-only:** [1](#0-0) 

```rust
fn get_header_by_height(&self, height: u64) -> ExtendedHeader {
    self.mainchain_height_to_header          // ← always the mainchain map
        .get(&height)
        .and_then(|hash| self.headers_pool.get(&hash))
        .unwrap_or_else(|| env::panic_str(ERR_KEY_NOT_EXIST))
}
```

There is no fork-aware path. The function has no knowledge of which chain is being validated.

**Call site in `get_next_work_required`:** [2](#0-1) 

```rust
let first_block_height = prev_block_header.block_height - blocks_to_go_back;
let interval_tail_extend_header = blocks_getter.get_header_by_height(first_block_height);
calculate_next_work_required(
    config,
    prev_block_header,
    interval_tail_extend_header.block_header.time.into(),  // ← mainchain timestamp, not fork's
)
```

`calculate_next_work_required` uses `first_block_time` to compute `actual_time_taken = T_prev − T_first`. If `T_first` is the mainchain block's timestamp rather than the fork ancestor's timestamp, the computed `bits` diverges from what the source chain would produce.

**Difficulty calculation sensitivity:** [3](#0-2) 

`actual_time_taken` is clamped to `[pow_target_timespan/4, pow_target_timespan*4]`, giving a maximum 4× swing in either direction. By controlling the fork ancestor's timestamp relative to the mainchain's block at the same height, an attacker can push `actual_time_taken` to the maximum, yielding a 4× easier `bits` than the source chain would compute — or, combined with the correct value being at the minimum clamp, a 16× total divergence.

---

### Impact Explanation

1. The contract stores a fork block whose `bits` field the Litecoin source chain would reject as invalid.
2. If that fork accumulates more `chain_work` than the mainchain (feasible on testnet with `pow_allow_min_difficulty_blocks = true`), `submit_block_header_inner` triggers `reorg_chain`, promoting the invalid fork to the mainchain in contract state.
3. A downstream bridge calling `verify_transaction_inclusion` or `verify_transaction_inclusion_v2` against the now-canonical fork would return `true` for transactions that are not canonical on the real Litecoin network, enabling fund theft. [4](#0-3) 

---

### Likelihood Explanation

**Testnet deployment is the realistic target.** Litecoin testnet has `pow_allow_min_difficulty_blocks = true`: [5](#0-4) 

This means any block whose timestamp exceeds `prev.time + 2 × 150s` is accepted at `proof_of_work_limit_bits = 0x1e0fffff` — trivially solvable with commodity hardware. The attacker can:

- Build a fork of arbitrary length using min-difficulty blocks.
- Set the fork's block at `first_block_height` to a timestamp significantly later than the mainchain block at the same height (within the `MAX_FUTURE_BLOCK_TIME_LOCAL = 2h` window at submission time, but the mainchain block may be days old).
- Craft `T_prev` so that `T_prev − T_main_first` hits the `4 × pow_target_timespan` cap, yielding the minimum possible `bits`.

The honest relayer submits fork blocks: the contract's `submit_block_header_inner` fork path and `reorg_chain` logic only trigger if the relayer actually delivers fork headers, confirming this is an intended submission path. [6](#0-5) 

The `#[trusted_relayer]` gate on `submit_blocks` does not block this attack: the attacker broadcasts scrypt-valid headers to the Litecoin testnet P2P network; the honest, registered relayer picks them up and submits them. No relayer key compromise is required. [7](#0-6) 

---

### Recommendation

`get_header_by_height` must be replaced with a fork-aware ancestor walk. The correct approach is to traverse `prev_block_hash` links through `headers_pool` starting from `prev_block_header` until reaching `first_block_height`, rather than indexing into `mainchain_height_to_header`. This mirrors how Litecoin Core itself walks the `CBlockIndex` chain pointer rather than a height-indexed map.

Alternatively, pass the fork-tip's ancestor at `first_block_height` explicitly as a parameter to `get_next_work_required`, resolved by the caller via `get_prev_header` traversal before the PoW check.

---

### Proof of Concept

```
Setup:
  Litecoin testnet, difficulty_adjustment_interval = 2016
  Mainchain tip at height H-1 (H = next retarget boundary)
  Mainchain block at height H-2017 has timestamp T_main = 1_700_000_000

Attack:
  1. Attacker builds fork diverging at H-2017.
     Fork block at H-2017: timestamp T_fork = 1_700_000_000 + 907_200
                                            = T_main + 3×302_400  (3 × pow_target_timespan)
     (Valid: submitted when current time ≈ T_fork, within 2h window)

  2. Fork blocks H-2016 … H-1: all min-difficulty (timestamps spaced >300s apart).
     Fork block at H-1: timestamp T_prev = T_fork + 7_200  (2h future)

  3. Attacker submits fork block at H.
     Contract calls get_next_work_required:
       first_block_height = H-2017
       get_header_by_height(H-2017) → mainchain block, time = T_main   ← BUG
       actual_time_taken = T_prev − T_main
                         = (T_fork + 7_200) − T_main
                         = 907_200 + 7_200 = 914_400 s
       clamped to 4 × 302_400 = 1_209_600 s  → maximum difficulty reduction
       accepted bits = proof_of_work_limit_bits (0x1e0fffff)

  Correct computation (source chain):
       actual_time_taken = T_prev − T_fork = 7_200 s
       clamped to pow_target_timespan/4 = 75_600 s  → maximum difficulty increase
       expected bits = much harder than 0x1e0fffff

  4. Contract stores fork block at H with bits=0x1e0fffff.
     Source chain rejects this block (wrong bits).

  5. Attacker extends fork past mainchain chainwork using min-difficulty blocks.
     reorg_chain() fires; fork becomes contract's mainchain.

  6. Bridge calls verify_transaction_inclusion for attacker's deposit-double-spend tx.
     Returns true. Bridge releases funds.
```

### Citations

**File:** contract/src/lib.rs (L168-179)
```rust
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
```

**File:** contract/src/lib.rs (L549-567)
```rust
        } else {
            log!("Block {}: saving to fork", current_header.block_hash);
            // Fork submission
            let main_chain_tip_header = self
                .headers_pool
                .get(&self.mainchain_tip_blockhash)
                .unwrap_or_else(|| env::panic_str("tip should be in a header pool"));

            let last_main_chain_block_height = main_chain_tip_header.block_height;
            let total_main_chain_chainwork = main_chain_tip_header.chain_work;

            self.store_fork_header(&current_header);

            // Current chainwork is higher than on a current mainchain, let's promote the fork
            if current_header.chain_work > total_main_chain_chainwork {
                log!("Chain reorg");
                self.reorg_chain(current_header, last_main_chain_block_height);
            }
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

**File:** contract/src/litecoin.rs (L86-93)
```rust
    let first_block_height = prev_block_header.block_height - blocks_to_go_back;

    let interval_tail_extend_header = blocks_getter.get_header_by_height(first_block_height);
    calculate_next_work_required(
        config,
        prev_block_header,
        interval_tail_extend_header.block_header.time.into(),
    )
```

**File:** contract/src/litecoin.rs (L104-110)
```rust
    let mut actual_time_taken: i64 = prev_block_time - first_block_time;
    if actual_time_taken < config.pow_target_timespan / 4 {
        actual_time_taken = config.pow_target_timespan / 4;
    }
    if actual_time_taken > config.pow_target_timespan * 4 {
        actual_time_taken = config.pow_target_timespan * 4;
    }
```

**File:** btc-types/src/network.rs (L66-77)
```rust
        Network::Testnet => NetworkConfig {
            difficulty_adjustment_interval: 2016,
            pow_target_timespan: 2016 * 150,
            proof_of_work_limit_bits: 0x1e0fffff,
            pow_target_spacing: 150, // 2.5 minutes
            pow_allow_min_difficulty_blocks: true,
            pow_limit: U256::new(
                0x0000_0fff_ffff_ffff_ffff_ffff_ffff_ffff,
                0xffff_ffff_ffff_ffff_ffff_ffff_ffff_ffff,
            ),
        },
    }
```
