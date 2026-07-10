### Title
Fork-Block Difficulty Calculated Against Mainchain Ancestor Instead of Actual Fork Ancestor — (`contract/src/lib.rs`, `contract/src/bitcoin.rs`, `contract/src/litecoin.rs`, `contract/src/dogecoin.rs`)

---

### Summary

`get_header_by_height` unconditionally reads from `mainchain_height_to_header`. When a fork block falls on a difficulty-retarget boundary, all three chain implementations (Bitcoin, Litecoin, Dogecoin) call this function to retrieve the interval-start block's timestamp. Because the fork may diverge before that boundary, the timestamp used belongs to the **mainchain** block at that height, not the fork's actual ancestor. An unprivileged NEAR caller (proof submitter / relayer) can exploit this desynchronization to submit fork blocks whose `bits` field satisfies the contract's (incorrect) difficulty check but would be rejected by real Bitcoin consensus — or vice versa — corrupting the light client's canonical-chain state and causing `verify_transaction_inclusion` to return wrong results.

---

### Finding Description

`BlocksGetter::get_header_by_height` is implemented as:

```rust
// contract/src/lib.rs  lines 677-682
fn get_header_by_height(&self, height: u64) -> ExtendedHeader {
    self.mainchain_height_to_header          // ← always mainchain
        .get(&height)
        .and_then(|hash| self.headers_pool.get(&hash))
        .unwrap_or_else(|| env::panic_str(ERR_KEY_NOT_EXIST))
}
``` [1](#0-0) 

This function is called at every retarget boundary by all three chain-specific difficulty calculators:

**Bitcoin** (`bitcoin.rs` lines 78–86):
```rust
let first_block_height =
    prev_block_header.block_height - (config.difficulty_adjustment_interval - 1);
let interval_tail_extend_header = blocks_getter.get_header_by_height(first_block_height);
calculate_next_work_required(config, prev_block_header,
    interval_tail_extend_header.block_header.time.into())
``` [2](#0-1) 

**Litecoin** (`litecoin.rs` lines 86–93) — identical pattern: [3](#0-2) 

**Dogecoin** (`dogecoin.rs` lines 291–297) — the codebase itself flags this with a `TODO`:
```rust
// TODO: check if it is correct to get block header by height from mainchain
//       without looping to find the ancestor
let first_block_time = blocks_getter
    .get_header_by_height(height_first)
    .block_header
    .time;
``` [4](#0-3) 

The PoW check is performed **before** the fork block is stored, so `mainchain_height_to_header` still reflects the old mainchain at the moment of the check: [5](#0-4) 

When a fork diverges at or before `height_first`, the mainchain block at `height_first` and the fork's actual ancestor at `height_first` are **different blocks with different timestamps**. The `actual_time_taken` fed into `calculate_next_work_required` is therefore wrong, producing an incorrect `expected_bits`. The contract then enforces:

```rust
require!(expected_bits == block_header.bits, "bad-diffbits: ...");
``` [6](#0-5) 

So the contract accepts a fork block whose `bits` matches the **wrong** expected value, not the value Bitcoin consensus would require.

---

### Impact Explanation

1. **Invalid header acceptance**: The contract accepts fork blocks carrying a `bits` value that real Bitcoin nodes would reject (or rejects valid ones). The light client's `headers_pool` and `mainchain_height_to_header` maps are populated with headers that do not conform to Bitcoin consensus.

2. **Reorg to a fraudulent chain**: If the attacker crafts fork timestamps so that the contract computes an *easier* `expected_bits` than consensus requires, the attacker can mine fork blocks with lower real PoW cost. Accumulated chainwork from these easier blocks can exceed the mainchain's chainwork, triggering `reorg_chain` and replacing the canonical chain with the attacker's fraudulent fork. [7](#0-6) 

3. **False SPV proofs**: After a reorg to the fraudulent fork, `verify_transaction_inclusion` and `verify_transaction_inclusion_v2` look up block hashes via `mainchain_header_to_height`, which now maps to attacker-controlled headers. Any transaction the attacker includes in those headers will verify as confirmed, enabling cross-chain bridge fraud. [8](#0-7) 

---

### Likelihood Explanation

- **Entry point is fully unprivileged**: `submit_blocks` is callable by any NEAR account (subject only to the `trusted_relayer` stake check, which is a configurable economic barrier, not a cryptographic one). [9](#0-8) 
- **Trigger condition is routine**: Any fork that diverges before a 2016-block retarget boundary (Bitcoin/Litecoin) or any fork at all (Dogecoin, where `difficulty_adjustment_interval = 1`) hits this path. [10](#0-9) 
- **Attacker controls fork timestamps**: Block timestamps are attacker-supplied fields, constrained only by MTP (must exceed median of last 11 blocks) and a 2-hour future cap. The attacker can freely choose timestamps to maximize the divergence between the mainchain block's timestamp at `height_first` and their fork ancestor's timestamp, steering `expected_bits` in the desired direction.
- **The codebase acknowledges the issue**: The `TODO` comment in `dogecoin.rs` line 291 confirms the developers identified this as an open correctness question.

---

### Recommendation

Replace `get_header_by_height` (mainchain lookup) with an ancestor-walk that follows `prev_block_hash` links from the block being validated back to `height_first`. This is the approach used by Bitcoin Core (`GetAncestor`). The walk must operate entirely within `headers_pool` (which stores both mainchain and fork blocks) and must not consult `mainchain_height_to_header` at all during fork-block difficulty validation.

---

### Proof of Concept

**Setup (Bitcoin mainnet, `difficulty_adjustment_interval = 2016`):**

1. The mainchain is at height 4031 (second retarget boundary). Mainchain block at height 2016 (`height_first`) has timestamp `T_main`.
2. Attacker submits a fork starting at height 2015 (diverges one block before `height_first`). The fork's block at height 2016 has timestamp `T_fork`, where `T_fork << T_main` (attacker sets it to the minimum allowed by MTP).
3. When the attacker submits the fork block at height 4032 (the next retarget boundary), `get_next_work_required` calls `get_header_by_height(2016)`, which returns the **mainchain** block with timestamp `T_main`.
4. `actual_time_taken = T_prev_fork - T_main`. Because `T_main > T_fork`, this is smaller than the true elapsed time `T_prev_fork - T_fork`, making the computed target **easier** than consensus requires.
5. The contract enforces `block_header.bits == expected_bits_easy`. The attacker mines a block satisfying `hash <= target_from_bits(expected_bits_easy)` — less work than real Bitcoin consensus demands.
6. The contract accepts the block. The attacker repeats for subsequent retarget boundaries, accumulating chainwork faster than the honest mainchain.
7. Once `fork.chain_work > mainchain.chain_work`, `reorg_chain` is triggered, replacing the canonical chain with the attacker's low-difficulty fork.
8. `verify_transaction_inclusion` now confirms attacker-fabricated transactions against this fraudulent chain.

### Citations

**File:** contract/src/lib.rs (L166-198)
```rust
    #[payable]
    #[pause]
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

**File:** contract/src/lib.rs (L299-322)
```rust
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

**File:** contract/src/lib.rs (L677-682)
```rust
    fn get_header_by_height(&self, height: u64) -> ExtendedHeader {
        self.mainchain_height_to_header
            .get(&height)
            .and_then(|hash| self.headers_pool.get(&hash))
            .unwrap_or_else(|| env::panic_str(ERR_KEY_NOT_EXIST))
    }
```

**File:** contract/src/bitcoin.rs (L23-26)
```rust
        require!(
            expected_bits == block_header.bits,
            "bad-diffbits: incorrect proof of work"
        );
```

**File:** contract/src/bitcoin.rs (L78-87)
```rust
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

**File:** contract/src/dogecoin.rs (L291-297)
```rust
    // TODO: check if it is correct to get block header by height from mainchain without looping to find the ancestor
    let first_block_time = blocks_getter
        .get_header_by_height(height_first)
        .block_header
        .time;

    calculate_next_work_required(config, prev_block_header, i64::from(first_block_time))
```

**File:** btc-types/src/network.rs (L83-84)
```rust
            difficulty_adjustment_interval: 1,
            pow_target_timespan: 60,
```
