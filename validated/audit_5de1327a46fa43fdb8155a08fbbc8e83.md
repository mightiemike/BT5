### Title
Difficulty Calculation for Fork Blocks Reads Stale Main-Chain State Instead of Fork-Chain Ancestor — (`File: contract/src/dogecoin.rs`)

### Summary

When computing the expected difficulty for a fork block, `get_next_work_required` in the Dogecoin module calls `blocks_getter.get_header_by_height(height_first)`, which always returns the block at that height from the **current main chain** (`mainchain_height_to_header`). For any fork block submitted at height ≥ H+2 (where H is the fork divergence point), the block at `height_first` on the main chain is a **different block** than the fork chain's ancestor at that height. The contract reads the wrong block's timestamp, producing an incorrect expected difficulty. An unprivileged proof submitter or relayer can exploit this to get fork blocks accepted with lower-than-required PoW difficulty, and if such a fork accumulates sufficient chain work, a reorg promotes an invalid chain to mainchain, corrupting `verify_transaction_inclusion` results.

The developers themselves flagged this with a TODO comment directly at the vulnerable line.

---

### Finding Description

In `get_next_work_required` (Dogecoin, `contract/src/dogecoin.rs`), when a difficulty-adjustment boundary is reached, the function computes `height_first` as the height of the block that starts the retarget window and then fetches it:

```rust
// TODO: check if it is correct to get block header by height from mainchain
// without looping to find the ancestor
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

This always returns the **main-chain** block at that height. For Dogecoin after block 145,000, `difficulty_adjustment_interval = 1`, so `blocks_to_go_back = 1` for every block: [3](#0-2) 

This means `height_first = prev_block_header.block_height - 1`. Consider a fork that diverges from the main chain at height H:

- Fork block 1 is at height H (different hash from main-chain block at H, but `height_first = H-1` — still a shared ancestor, so the lookup is correct).
- Fork block 2 is at height H+1; `height_first = H` — still correct.
- **Fork block 3** is at height H+2; `height_first = H+1`. The main-chain block at H+1 is a **different block** from the fork's block at H+1. The contract reads the wrong block's timestamp.

The difficulty calculation uses this timestamp as `first_block_time`:

```rust
let modulated_timespan = i64::from(prev_block_header.block_header.time) - first_block_time;
``` [4](#0-3) 

If the main-chain block at H+1 has an earlier timestamp than the fork's block at H+1, `modulated_timespan` is inflated, producing a higher target (easier difficulty). The contract then enforces this wrong `expected_bits`:

```rust
require!(
    expected_bits == block_header.bits,
    ...
);
``` [5](#0-4) 

An attacker who controls the fork blocks can craft timestamps on fork blocks 1 and 2 such that the main-chain block at H+1 has an earlier timestamp than the fork's block at H+1. This inflates the computed timespan for fork block 3 onward, lowering the required difficulty. The attacker then submits fork block 3 with `bits` set to the easier (inflated) target, and the contract accepts it.

The same structural issue exists in `contract/src/bitcoin.rs` and `contract/src/litecoin.rs`: [6](#0-5) [7](#0-6) 

However, for Bitcoin and Litecoin the retarget window is 2016 blocks, so the fork would need to be ≥2016 blocks deep before the wrong block is read — making it far less realistic. For Dogecoin post-145k the window is 1 block, so the bug is triggered by any fork ≥3 blocks deep.

---

### Impact Explanation

Accepted fork blocks with under-difficulty PoW are stored in `headers_pool`. If the fork accumulates enough chain work (even at reduced difficulty), `submit_block_header_inner` triggers `reorg_chain`, promoting the invalid fork to the main chain: [8](#0-7) 

After the reorg, `mainchain_header_to_height` maps heights to the invalid fork blocks. Any subsequent call to `verify_transaction_inclusion` or `verify_transaction_inclusion_v2` will confirm transactions against this invalid chain, returning `true` for transactions that are not in the real Dogecoin chain: [9](#0-8) 

This is a proof-verification forgery: the canonical chain value and header acceptance decision are both corrupted.

---

### Likelihood Explanation

For Dogecoin (post-block 145,000), the bug is triggered by any fork ≥3 blocks deep. A relayer or any NEAR caller with access to `submit_blocks` can submit such a fork. No privileged role is required — `submit_blocks` is a public payable function gated only by the `trusted_relayer` macro and the `Pausable` guard, both of which are bypassable by a registered relayer or a caller with `UnrestrictedSubmitBlocks` role. The attacker controls fork block timestamps within the MTP constraint, giving them a concrete lever to inflate the computed timespan.

---

### Recommendation

Replace `get_header_by_height` with a chain-walk that follows `prev_block_hash` links from the fork's `prev_block_header` back `blocks_to_go_back` steps, exactly as Zcash's `zcash_get_next_work_required` does with `get_prev_header`: [10](#0-9) 

This ensures the difficulty calculation always uses the fork chain's own ancestor at `height_first`, not the main chain's block at that height. The existing TODO comment at the vulnerable line already identifies this as an open question that must be resolved.

---

### Proof of Concept

1. Deploy the Dogecoin variant of the contract (`--features dogecoin`) with a genesis at height ≥ 145,001.
2. Submit the main chain up to height H+1. Record the timestamp of the main-chain block at height H+1 as `T_main`.
3. Craft fork block 1 at height H (diverges from main chain; timestamp `T_fork_1 > T_main`).
4. Craft fork block 2 at height H+1 (timestamp `T_fork_2`).
5. Craft fork block 3 at height H+2. The contract will compute `height_first = H+1` and fetch the **main-chain** block at H+1 (timestamp `T_main`). The computed timespan is `T_fork_2 - T_main`. Since `T_main < T_fork_1 < T_fork_2`, this timespan is larger than the correct `T_fork_2 - T_fork_1`, producing a higher (easier) target. Set `bits` in fork block 3 to this easier target and provide a PoW hash that satisfies it but would not satisfy the correct target.
6. Call `submit_blocks([fork_block_1, fork_block_2, fork_block_3])`. The contract accepts fork block 3 despite its under-difficulty PoW.
7. Continue submitting fork blocks with the inflated target until the fork's chain work exceeds the main chain's, triggering a reorg.
8. Call `verify_transaction_inclusion` with a transaction from the invalid fork — it returns `true`.

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

**File:** contract/src/dogecoin.rs (L291-295)
```rust
    // TODO: check if it is correct to get block header by height from mainchain without looping to find the ancestor
    let first_block_time = blocks_getter
        .get_header_by_height(height_first)
        .block_header
        .time;
```

**File:** contract/src/dogecoin.rs (L307-309)
```rust
    let modulated_timespan = i64::from(prev_block_header.block_header.time) - first_block_time;

    let mut modulated_timespan = retarget_timespan + (modulated_timespan - retarget_timespan) / 8;
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

**File:** contract/src/bitcoin.rs (L81-86)
```rust
    let interval_tail_extend_header = blocks_getter.get_header_by_height(first_block_height);
    calculate_next_work_required(
        config,
        prev_block_header,
        interval_tail_extend_header.block_header.time.into(),
    )
```

**File:** contract/src/litecoin.rs (L88-93)
```rust
    let interval_tail_extend_header = blocks_getter.get_header_by_height(first_block_height);
    calculate_next_work_required(
        config,
        prev_block_header,
        interval_tail_extend_header.block_header.time.into(),
    )
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
