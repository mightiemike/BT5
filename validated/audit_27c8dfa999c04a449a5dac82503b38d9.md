### Title
Intra-Batch Reorg Poisons Retarget Boundary Lookup — (`contract/src/lib.rs`, `contract/src/bitcoin.rs`)

### Summary

Within a single `submit_blocks` call, `reorg_chain` mutates `mainchain_height_to_header` in-place. A subsequent header in the same batch that falls on a retarget boundary calls `get_header_by_height`, which reads the now-mutated map. The difficulty check therefore uses the post-reorg boundary block's timestamp instead of the pre-reorg one, allowing an attacker to get a retarget-boundary block accepted with bits that would be rejected under the correct chain state.

---

### Finding Description

**Exact execution path:**

`submit_blocks` (lib.rs:177–179) iterates over headers sequentially, calling `submit_block_header` for each: [1](#0-0) 

Inside `submit_block_header`, the PoW/difficulty check fires **before** the header is committed to state: [2](#0-1) 

`check_target` → `check_pow` → `get_next_work_required` — at a retarget boundary — calls `get_header_by_height(first_block_height)`: [3](#0-2) 

`get_header_by_height` reads directly from `mainchain_height_to_header`: [4](#0-3) 

After the check passes, `submit_block_header_inner` may call `reorg_chain`, which **overwrites** `mainchain_height_to_header` entries for every height from the fork divergence point up to the fork tip: [5](#0-4) 

When the **next** header in the same batch is processed and falls on a retarget boundary, `get_header_by_height` now returns the fork's block at the boundary height — a block with an attacker-controlled timestamp — not the original mainchain block.

---

### Impact Explanation

`calculate_next_work_required` derives `actual_time_taken` from the boundary block's timestamp: [6](#0-5) 

By crafting the fork's block at height `(N-1)*2016` with a timestamp that compresses `actual_time_taken` toward the lower clamp (`pow_target_timespan / 4`), the attacker maximally inflates the target (easiest allowed difficulty). The retarget-boundary header is then accepted with bits encoding that inflated target. `chain_work` for all subsequent headers is computed from this corrupted bits value, permanently corrupting the canonical chain-work accumulator and undermining all downstream `verify_transaction_inclusion` confirmations.

---

### Likelihood Explanation

The precondition is that the caller holds `Role::UnrestrictedSubmitBlocks` or is a staked trusted relayer (the `#[trusted_relayer]` gate with `bypass_roles(Role::UnrestrictedSubmitBlocks)`): [7](#0-6) 

A staked trusted relayer can submit arbitrary header batches. The additional requirement — a fork chain with chainwork exceeding the current mainchain tip — is computationally expensive on Bitcoin mainnet but is feasible on testnet deployments or any chain where the contract is initialized with `skip_pow_verification = false` but at low difficulty. The code-level bug is unconditional; the economic barrier is the only mitigation.

---

### Recommendation

Snapshot the relevant boundary block's timestamp **before** the batch loop begins, or re-read it from `headers_pool` via the block hash stored at the start of the call rather than via the height-indexed map. Concretely, in `get_next_work_required`, resolve the boundary block by walking the `prev_block_hash` chain from `prev_block_header` backward by `difficulty_adjustment_interval - 1` steps (using `get_prev_header`), which is immune to mid-batch reorg mutations of `mainchain_height_to_header`.

---

### Proof of Concept

```
State before call:
  mainchain: [B0(h=0)] - [B2016(h=2016, time=T_boundary)] - ... - [B4031(h=4031)]
  headers_pool contains fork blocks F0..F4032 diverging at h=0,
    with F2016.time = T_boundary - MAX_COMPRESSION (attacker-chosen)
    and fork chainwork > mainchain chainwork at h=4031

submit_blocks([F4032, R4032])
  // F4032 is the fork tip at height 4032 (triggers reorg)
  // R4032 is a retarget-boundary header at height 4032 whose prev is F4031

Step 1 — process F4032:
  check_pow(F4032): retarget boundary? No (height 4032 % 2016 == 0 but prev=4031,
    (4031+1) % 2016 == 0 → YES, but F4032's bits are crafted for the fork's own
    boundary block, which is already in headers_pool — passes)
  submit_block_header_inner → reorg_chain:
    mainchain_height_to_header[2016] ← F2016.hash  // ← mutation

Step 2 — process R4032 (retarget boundary, prev = F4031 at height 4031):
  get_next_work_required:
    first_block_height = 4031 - 2015 = 2016
    get_header_by_height(2016) → F2016  // post-reorg, attacker-controlled timestamp
    calculate_next_work_required(prev=F4031, first_block_time=F2016.time)
      → expected_bits = EASY_BITS  // inflated target due to compressed timespan
  R4032.bits == EASY_BITS → check passes
  R4032 hash ≤ target(EASY_BITS) → PoW passes (easy to mine)
  R4032 accepted; chain_work corrupted
```

### Citations

**File:** contract/src/lib.rs (L120-124)
```rust
#[trusted_relayer(
    bypass_roles(Role::DAO, Role::UnrestrictedSubmitBlocks),
    manager_roles(Role::DAO, Role::RelayerManager),
    config_roles(Role::DAO)
)]
```

**File:** contract/src/lib.rs (L177-179)
```rust
        for header in headers {
            self.submit_block_header(header, self.skip_pow_verification);
        }
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

**File:** contract/src/lib.rs (L626-630)
```rust
            let main_chain_block = self
                .mainchain_height_to_header
                .insert(&current_height, &current_block_hash);
            self.mainchain_header_to_height
                .insert(&current_block_hash, &current_height);
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

**File:** contract/src/bitcoin.rs (L95-103)
```rust
    let prev_block_time: i64 = prev_block_header.block_header.time.into();

    let mut actual_time_taken: i64 = prev_block_time - first_block_time;
    if actual_time_taken < config.pow_target_timespan / 4 {
        actual_time_taken = config.pow_target_timespan / 4;
    }
    if actual_time_taken > config.pow_target_timespan * 4 {
        actual_time_taken = config.pow_target_timespan * 4;
    }
```
