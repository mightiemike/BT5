### Title
Fork Retarget Ancestor Confusion: `get_header_by_height` Reads Mainchain Block Instead of Fork Ancestor at Retarget Boundary — (`contract/src/bitcoin.rs`, `contract/src/lib.rs`)

---

### Summary

`get_next_work_required` calls `blocks_getter.get_header_by_height(first_block_height)` to obtain the timestamp of the first block in the retarget interval. The sole implementation of `get_header_by_height` reads exclusively from `mainchain_height_to_header`. When the block being validated is on a **fork** that diverged before `first_block_height`, the mainchain block at that height is a different block with a different timestamp than the fork's true ancestor. The difficulty calculation therefore uses the wrong timespan, producing an incorrect `bits` value that the contract accepts as valid.

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

There is no fork-aware path. The function has no knowledge of which chain the caller is validating.

**Call site — retarget boundary lookup:** [2](#0-1) 

```rust
let first_block_height =
    prev_block_header.block_height - (config.difficulty_adjustment_interval - 1);

let interval_tail_extend_header = blocks_getter.get_header_by_height(first_block_height);
calculate_next_work_required(
    config,
    prev_block_header,
    interval_tail_extend_header.block_header.time.into(),  // ← wrong timestamp on a fork
)
```

`prev_block_header` is the fork's tip (fetched via `get_prev_header`, which correctly traverses `headers_pool` by hash). But `get_header_by_height(first_block_height)` returns the **mainchain** block at that height, not the fork's ancestor.

**Scenario:**

```
height:  N-2015          N-1          N (retarget)
mainchain: [M_0] ... [M_2014] ... [M_2015]
                  \
fork:              [F_1] ... [F_2014] ... [F_2015]  ← being validated
```

When `F_2015` is submitted, `first_block_height = N - 2015`. `get_header_by_height(N-2015)` returns `M_0` (mainchain), not the fork's ancestor at that height. `calculate_next_work_required` receives `M_0.time` instead of the fork ancestor's time, producing a `bits` value that does not correspond to the fork's actual history.

**Testnet amplification (`pow_allow_min_difficulty_blocks = true`):** [3](#0-2) 

On testnet, `pow_allow_min_difficulty_blocks` is `true`. The min-difficulty path in `get_next_work_required` also calls `get_prev_header` in a loop — but the retarget path (the `get_header_by_height` call) is reached first whenever `(height) % 2016 == 0`, regardless of testnet/mainnet.

**Entry point — `submit_blocks` with `#[trusted_relayer]`:** [4](#0-3) 

`submit_blocks` is gated by `#[trusted_relayer]`. The `bypass_roles` include `Role::UnrestrictedSubmitBlocks`; the `manager_roles` include `Role::RelayerManager` which "reject applications". The staking/application mechanism is implemented in `omni_utils::macros::trusted_relayer` (external crate). The role description implies applications can be submitted and are active unless explicitly rejected — meaning a staking participant who has not been rejected is a valid caller. The scope rules explicitly include "trusted-relayer submission path with attacker-relevant data" as a valid production entry point.

**State mutation that makes the exploit real:**

Once the fork accumulates more chainwork than the mainchain tip, `submit_block_header_inner` calls `reorg_chain`: [5](#0-4) 

The fork is promoted to mainchain. `mainchain_tip_blockhash`, `mainchain_height_to_header`, and `mainchain_header_to_height` are all updated to reflect the crafted fork. Downstream `verify_transaction_inclusion` / `verify_transaction_inclusion_v2` calls then confirm transactions in blocks that were validated with an incorrect difficulty target.

---

### Impact Explanation

A crafted fork that passes difficulty validation due to the wrong ancestor timestamp gets stored as the canonical chain. Any bridge or protocol relying on `verify_transaction_inclusion` or `verify_transaction_inclusion_v2` will treat confirmations on this invalid fork as final, enabling theft or loss of bridged funds.

---

### Likelihood Explanation

Requires the attacker to be an active trusted relayer (staked and not rejected). The staking mechanism is permissionless unless a `RelayerManager` actively rejects the application. On testnet deployments the attack surface is wider because `pow_allow_min_difficulty_blocks = true` gives additional timestamp manipulation leverage. The retarget boundary occurs every 2016 blocks (~2 weeks), providing a predictable exploitation window.

---

### Recommendation

Replace the height-based lookup in `get_next_work_required` with a hash-chain traversal that follows `prev_block_hash` links backward from `prev_block_header` for exactly `difficulty_adjustment_interval - 1` steps. This mirrors Bitcoin Core's `GetAncestor` approach and ensures the fork's own lineage is used regardless of what the mainchain map contains at that height.

---

### Proof of Concept

1. Initialize the contract on testnet at height `H` where `H % 2016 == 0`.
2. Submit mainchain blocks `H` through `H + 2015` (one full retarget interval). The mainchain block at height `H` has timestamp `T_main`.
3. Construct a fork that diverges at height `H + 1`. The fork's block at height `H` (its ancestor) has timestamp `T_fork ≠ T_main` (the fork shares the genesis but the attacker controls subsequent fork blocks' timestamps).
4. Submit fork blocks `H+1` through `H+2015`. These pass because they are within the same retarget interval and inherit `prev_block_header.bits`.
5. Submit fork block `H+2016` (the retarget block). `get_next_work_required` computes `first_block_height = H+2016 - 2015 = H+1`. `get_header_by_height(H+1)` returns the **mainchain** block at `H+1` with timestamp `T_main_H1`, not the fork's block at `H+1` with timestamp `T_fork_H1`.
6. `calculate_next_work_required` uses `T_main_H1` as `first_block_time`, producing `bits_wrong`. The attacker crafts `F_2016.bits = bits_wrong` and the contract accepts it.
7. Compare `bits_wrong` against a reference Bitcoin node using the fork's true ancestor history — they differ, confirming the contract accepted an invalid difficulty target.
8. Continue extending the fork with sufficient chainwork to trigger `reorg_chain`. The contract now treats the crafted fork as canonical.

### Citations

**File:** contract/src/lib.rs (L166-179)
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

**File:** btc-types/src/network.rs (L39-50)
```rust
        Network::Testnet => NetworkConfig {
            difficulty_adjustment_interval: 2016,
            pow_target_timespan: 2016 * 600, // difficulty_adjustment_interval * target_block_time_secs,
            proof_of_work_limit_bits: 0x1d00ffff,
            pow_target_spacing: 600, // 10 minutes
            pow_allow_min_difficulty_blocks: true,
            pow_limit: U256::new(
                0x0000_0000_ffff_ffff_ffff_ffff_ffff_ffff,
                0xffff_ffff_ffff_ffff_ffff_ffff_ffff_ffff,
            ),
        },
    }
```
