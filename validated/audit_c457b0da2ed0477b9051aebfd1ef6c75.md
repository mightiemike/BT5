### Title
Fork Difficulty Retarget Uses Mainchain Ancestor Instead of Fork Ancestor — (`contract/src/dogecoin.rs`)

---

### Summary

`get_next_work_required` in the Dogecoin module calls `blocks_getter.get_header_by_height(height_first)` to obtain the timestamp of the period-start block for Digishield retargeting. The `BlocksGetter` implementation for `BtcLightClient` resolves this lookup exclusively through `mainchain_height_to_header`, so it always returns the **mainchain** block at that height — never the fork ancestor. When a fork diverges before `height_first`, the timestamp used for difficulty calculation is wrong, causing the contract to compute an incorrect `expected_bits` for every fork block at height ≥ 145,001. The developers themselves flagged this with a `TODO` comment at the exact line.

---

### Finding Description

**Entrypoint:** Any caller of `submit_blocks` (the public NEAR contract method) can submit Dogecoin fork headers. No access control restricts who may submit fork blocks.

**Execution path:**

1. `submit_blocks` → `submit_block_header` (dogecoin) → `check_target` → `check_pow` → `get_next_work_required`

2. Inside `get_next_work_required` (dogecoin.rs:244–295): for `prev_block_header.block_height >= 145_000`, `difficulty_adjustment_interval = 1`. Since `(height + 1) % 1 == 0` always, the early-return branch is never taken and the code always reaches the retarget calculation.

3. `blocks_to_go_back` is set to `1` (dogecoin.rs:280–283), so `height_first = prev_block_header.block_height - 1`.

4. The timestamp is fetched via:
   ```rust
   // TODO: check if it is correct to get block header by height from mainchain
   // without looping to find the ancestor
   let first_block_time = blocks_getter
       .get_header_by_height(height_first)   // line 293
       .block_header
       .time;
   ```

5. `get_header_by_height` (lib.rs:677–682) resolves only through `mainchain_height_to_header`:
   ```rust
   fn get_header_by_height(&self, height: u64) -> ExtendedHeader {
       self.mainchain_height_to_header
           .get(&height)
           .and_then(|hash| self.headers_pool.get(&hash))
           .unwrap_or_else(|| env::panic_str(ERR_KEY_NOT_EXIST))
   }
   ```
   Fork blocks are stored only in `headers_pool` (via `store_fork_header`, lib.rs:665–667), never in `mainchain_height_to_header`. There is no path through which `get_header_by_height` can return a fork ancestor.

**The desynchronization:** When a fork diverges at height H−2 or earlier, the fork block at height H−1 (call it `F_{H-1}`) has a different timestamp than the mainchain block at height H−1 (call it `M_{H-1}`). When the contract validates fork block `F_H`, it computes `expected_bits` using `time(M_{H-1})` instead of `time(F_{H-1})`. The resulting `expected_bits` is wrong.

`check_pow` then enforces `expected_bits == block_header.bits` (dogecoin.rs:27–33). This means:
- Fork blocks whose `bits` field matches the **true** Dogecoin-consensus difficulty are **rejected** (if the mainchain-based calculation differs).
- Fork blocks whose `bits` field matches the **contract's wrong** expected_bits are **accepted**, even though they would be rejected by the real Dogecoin network.

---

### Impact Explanation

**Correctness violation (primary impact):** The contract accepts fork blocks that do not satisfy Dogecoin's actual consensus difficulty rules, and rejects fork blocks that do. The light client's fundamental invariant — that it only tracks chains valid under Dogecoin consensus — is broken.

**Reorg to invalid chain:** If an attacker submits a fork chain whose blocks carry the wrong `bits` (matching the contract's mainchain-based calculation), and that fork accumulates more `chain_work` than the mainchain tip, `submit_block_header_inner` (lib.rs:563–565) triggers `reorg_chain`. After the reorg, `mainchain_tip_blockhash` points to a chain that is invalid on the real Dogecoin network. Subsequent calls to `verify_transaction_inclusion` will return `true` for transactions in this invalid chain, breaking SPV security for all downstream consumers.

**Difficulty manipulation range:** Digishield clamps `modulated_timespan` to `[retarget_timespan * 3/4, retarget_timespan * 3/2]` (dogecoin.rs:311–318), bounding the per-block difficulty swing to ±50%. A fork ancestor timestamp differing by more than `retarget_timespan / 4` from the mainchain ancestor will hit the clamp and produce a maximally shifted `expected_bits`. This is achievable in practice since fork blocks are independently mined and their timestamps are not constrained to match mainchain timestamps.

---

### Likelihood Explanation

- The bug is triggered for **every** fork block at height ≥ 145,001 where the fork diverged at least 2 blocks before the block being validated. This is the normal case for any non-trivial fork.
- No special privileges are required. `submit_blocks` is a public method.
- The developers explicitly acknowledged the issue with a `TODO` comment at the exact line of the bug (dogecoin.rs:291), confirming it is a known, unresolved design gap.
- The Dogecoin mainnet is well past height 145,000 (currently ~5.8M blocks), so the `new_difficulty_protocol` branch is always active.

---

### Recommendation

Replace the `get_header_by_height` call with a chain-traversal that walks back through `get_prev_header` from `prev_block_header` by `blocks_to_go_back` steps. This correctly resolves the period-start ancestor on the fork chain rather than the mainchain. The `get_prev_header` implementation already correctly follows `prev_block_hash` through `headers_pool` (lib.rs:671–675), so fork ancestors are reachable.

---

### Proof of Concept

**State setup (height ≥ 145,001, `new_difficulty_protocol` active):**

```
Mainchain: ... → M_{H-2} → M_{H-1} → M_H  (tip)
Fork:      ... → M_{H-2} → F_{H-1} → F_H  (fork tip, higher chain_work)
```

Where `time(F_{H-1}) ≠ time(M_{H-1})`.

**Step 1:** Submit mainchain blocks up to `M_H`. Contract state: `mainchain_height_to_header[H-1] = hash(M_{H-1})`.

**Step 2:** Submit fork block `F_{H-1}` (child of `M_{H-2}`). Stored in `headers_pool` only.

**Step 3:** Submit fork block `F_H` (child of `F_{H-1}`). `check_pow` calls `get_next_work_required`:
- `height_first = H - 1`
- `get_header_by_height(H-1)` returns `M_{H-1}` (mainchain block), not `F_{H-1}` (fork ancestor)
- `first_block_time = time(M_{H-1})` ← **wrong**
- `expected_bits` is computed from `time(M_{H-1})` instead of `time(F_{H-1})`

**Step 4:** Assert that `expected_bits` computed with `time(M_{H-1})` differs from `expected_bits` computed with `time(F_{H-1})`. The fork block `F_H` carrying the mainchain-based `bits` is accepted; a fork block carrying the true Dogecoin-consensus `bits` is rejected.

**Step 5:** If `F_H.chain_work > M_H.chain_work`, `reorg_chain` fires and the contract's mainchain becomes the fork chain — a chain invalid under Dogecoin consensus.

---

**Key code references:** [1](#0-0) [2](#0-1) [3](#0-2) [4](#0-3) [5](#0-4) [6](#0-5)

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

**File:** contract/src/dogecoin.rs (L244-249)
```rust
    let new_difficulty_protocol = prev_block_header.block_height >= 145_000;
    let difficulty_adjustment_interval = if new_difficulty_protocol {
        1
    } else {
        config.difficulty_adjustment_interval
    };
```

**File:** contract/src/dogecoin.rs (L291-297)
```rust
    // TODO: check if it is correct to get block header by height from mainchain without looping to find the ancestor
    let first_block_time = blocks_getter
        .get_header_by_height(height_first)
        .block_header
        .time;

    calculate_next_work_required(config, prev_block_header, i64::from(first_block_time))
```

**File:** contract/src/lib.rs (L560-566)
```rust
            self.store_fork_header(&current_header);

            // Current chainwork is higher than on a current mainchain, let's promote the fork
            if current_header.chain_work > total_main_chain_chainwork {
                log!("Chain reorg");
                self.reorg_chain(current_header, last_main_chain_block_height);
            }
```

**File:** contract/src/lib.rs (L665-667)
```rust
    fn store_fork_header(&mut self, header: &ExtendedHeader) {
        self.headers_pool.insert(&header.block_hash, header);
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
