### Title
Stale Mainchain Block Used as Difficulty Ancestor for Fork Block Validation - (`contract/src/dogecoin.rs`, `contract/src/bitcoin.rs`, `contract/src/litecoin.rs`)

---

### Summary

When validating a fork block at a difficulty-adjustment boundary, all three chain implementations (`bitcoin.rs`, `litecoin.rs`, `dogecoin.rs`) retrieve the "first block in the retarget interval" by looking up the **mainchain** block at that height via `get_header_by_height`. If the fork diverged before that height, the mainchain block at that height is a different block than the fork's actual ancestor. The stale mainchain timestamp is then fed into `calculate_next_work_required`, producing an incorrect `expected_bits`. A fork block whose `bits` field matches the wrong expected value passes the difficulty check even though it would fail against the correct ancestor.

---

### Finding Description

`get_header_by_height` is implemented as a strict mainchain lookup: [1](#0-0) 

```rust
fn get_header_by_height(&self, height: u64) -> ExtendedHeader {
    self.mainchain_height_to_header
        .get(&height)
        .and_then(|hash| self.headers_pool.get(&hash))
        .unwrap_or_else(|| env::panic_str(ERR_KEY_NOT_EXIST))
}
```

All three chain modules call this function to obtain the "interval tail" block when a difficulty retarget is due:

**Bitcoin** (`bitcoin.rs` line 81): [2](#0-1) 

**Litecoin** (`litecoin.rs` line 88): [3](#0-2) 

**Dogecoin** (`dogecoin.rs` lines 292–295), where the codebase itself flags the problem with a TODO: [4](#0-3) 

```rust
// TODO: check if it is correct to get block header by height from mainchain
// without looping to find the ancestor
let first_block_time = blocks_getter
    .get_header_by_height(height_first)
    .block_header
    .time;
```

The correct approach is to walk the fork's `prev_block_hash` chain back to `height_first`, because the mainchain block at that height is a sibling, not an ancestor, of the fork block being validated.

The stale timestamp is then passed directly into `calculate_next_work_required`: [5](#0-4) 

which computes `expected_bits` and is compared against the submitted block's `bits` field: [6](#0-5) 

The Dogecoin case is the most severe: after block 145,000 the `difficulty_adjustment_interval` is set to `1`, meaning **every** fork block triggers a retarget lookup: [7](#0-6) 

So for every Dogecoin fork block submitted after height 145,000, the difficulty is computed against the wrong ancestor.

---

### Impact Explanation

An attacker who submits a fork chain that diverges before `height_first` causes the contract to compute `expected_bits` from the mainchain block's timestamp rather than the fork's actual ancestor timestamp. The attacker observes what `expected_bits` the contract will derive (it is deterministic from on-chain state), sets their fork block's `bits` field to that value, and mines PoW against that target. If the correct ancestor would have produced a harder target (higher `bits` = lower difficulty number), the attacker mines the fork block with less work than the protocol requires. Repeated across a fork chain, this reduces the total chainwork needed to trigger a reorg, undermining the chainwork-based canonical-chain selection that `submit_block_header_inner` relies on: [8](#0-7) 

The corrupted state is the `mainchain_tip_blockhash` and the `mainchain_height_to_header` / `mainchain_header_to_height` maps, which downstream callers of `verify_transaction_inclusion` and `verify_transaction_inclusion_v2` depend on for SPV proof correctness. [9](#0-8) 

---

### Likelihood Explanation

The entry point is the public, unprivileged `submit_blocks` call: [10](#0-9) 

Any NEAR account can invoke it. The attacker only needs to:
1. Identify the current mainchain tip and the timestamp of the mainchain block at `height_first`.
2. Compute the `expected_bits` the contract will accept.
3. Mine a fork chain to that (potentially easier) target.

For Dogecoin post-145,000, this applies to every single fork block, making the attack surface continuous rather than periodic. For Bitcoin and Litecoin it applies only at 2016-block retarget boundaries, but those boundaries are predictable and the attack is still reachable.

---

### Recommendation

Replace the `get_header_by_height` call with an ancestor walk along `prev_block_hash` starting from `prev_block_header`, descending exactly `blocks_to_go_back` steps through the fork's own chain stored in `headers_pool`. This mirrors what Bitcoin Core does in `GetAncestor`. The correct block to use is the fork's ancestor at `height_first`, not the mainchain block at that height.

---

### Proof of Concept

1. Contract is initialized with a Dogecoin mainchain at height ≥ 145,001. Mainchain block at height H has timestamp `T_main`.
2. Attacker submits a fork block at height H−1 (diverging from mainchain at H−2). Fork ancestor at H−2 has timestamp `T_fork ≠ T_main`.
3. Attacker submits fork block at height H. `get_next_work_required` is called; `height_first = H−2`; `get_header_by_height(H−2)` returns the **mainchain** block with timestamp `T_main`.
4. `calculate_next_work_required` produces `expected_bits_wrong` based on `T_main`.
5. The correct calculation using `T_fork` would produce `expected_bits_correct` (harder, i.e., numerically smaller).
6. Attacker sets fork block's `bits = expected_bits_wrong` and mines PoW against that easier target.
7. `require!(expected_bits == block_header.bits)` passes.
8. The fork block is accepted with less work than the protocol requires, reducing the chainwork threshold needed to trigger a reorg via `reorg_chain`. [11](#0-10)

### Citations

**File:** contract/src/lib.rs (L167-172)
```rust
    #[pause]
    #[trusted_relayer]
    pub fn submit_blocks(
        &mut self,
        #[serializer(borsh)] headers: Vec<BlockHeader>,
    ) -> PromiseOrValue<()> {
```

**File:** contract/src/lib.rs (L299-302)
```rust
            .mainchain_header_to_height
            .get(&args.tx_block_blockhash)
            .unwrap_or_else(|| env::panic_str("block does not belong to the current main chain"));

```

**File:** contract/src/lib.rs (L562-566)
```rust
            // Current chainwork is higher than on a current mainchain, let's promote the fork
            if current_header.chain_work > total_main_chain_chainwork {
                log!("Chain reorg");
                self.reorg_chain(current_header, last_main_chain_block_height);
            }
```

**File:** contract/src/lib.rs (L575-647)
```rust
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
