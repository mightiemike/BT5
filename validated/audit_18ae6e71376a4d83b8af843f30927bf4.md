### Title
`gas_prices_to_hash` omits `l2_gas_consumed` and `next_l2_gas_price` from block hash for Starknet versions ≥ 0.14.0 — (`crates/starknet_api/src/block_hash/block_hash_calculator.rs`)

---

### Summary

The `gas_prices_to_hash` function used in `calculate_block_hash` does not include `l2_gas_consumed` and `next_l2_gas_price` in the block hash preimage for any Starknet version, including versions ≥ 0.14.0 where these fields are required. A developer-authored TODO comment in the same function explicitly acknowledges the omission. The `echonet` verification tool already passes both fields to its block-hash CLI, creating a divergence between the hash the sequencer commits and the hash the verifier expects.

---

### Finding Description

`gas_prices_to_hash` in `crates/starknet_api/src/block_hash/block_hash_calculator.rs` is the sole function that serialises gas-price data into the block hash preimage. For `BlockHashVersion >= V0_13_4` it hashes six values under the `STARKNET_GAS_PRICES0` domain tag:

```
l1_gas_price_wei, l1_gas_price_fri,
l1_data_gas_price_wei, l1_data_gas_price_fri,
l2_gas_price_wei, l2_gas_price_fri
```

The function carries an explicit TODO:

```rust
// TODO(Ayelet): add l2_gas_consumed, next_l2_gas_price after 0.14.0.
pub fn gas_prices_to_hash(...)
```

Both `l2_gas_consumed` and `next_l2_gas_price` are present in `BlockHeaderWithoutHash` and `StorageBlockHeader`, and are populated for every block. However, `PartialBlockHashComponents` — the struct fed into `calculate_block_hash` — does not carry these fields, so they are structurally excluded from the hash.

The `echonet/echo_center.py` verification tool already includes both fields when calling its block-hash CLI:

```python
"l2_gas_consumed": fee_market_info["l2_gas_consumed"],
"next_l2_gas_price": fee_market_info["next_l2_gas_price"],
```

The current production Starknet version in test fixtures is `0.14.4`, which is past the `0.14.0` threshold named in the TODO. No `BlockHashVersion` variant for `V0_14_x` exists in the Rust code, so all `0.14.x` blocks fall through to the `V0_13_4` branch, which omits the two fields.

The analog to the external report is direct: just as `hedgePositions` computed `newPosition = requiredPosition - _getTotalPerpPosition()` while silently ignoring `queuedPerpSize`, `gas_prices_to_hash` computes the gas-prices hash while silently ignoring `l2_gas_consumed` and `next_l2_gas_price`.

---

### Impact Explanation

`calculate_block_hash` is called in two production paths:

1. `PartialBlockHash::from_partial_block_hash_components` — produces the `ProposalCommitment` that validators sign during consensus.
2. `finalize_commitment_output` in the commitment manager — produces the canonical `BlockHash` written to storage and broadcast to L1.

A wrong block hash means:
- The hash stored on-chain diverges from what the echonet verifier and any external block-hash checker compute, causing proof-verification failures for every `0.14.x` block.
- The `validate_proof_block_hash` path inside `validate_proof_facts` reads the stored block hash to verify SNOS proof facts; a wrong stored hash causes valid proofs to be rejected or invalid proofs to pass.
- Consensus nodes that independently recompute the partial block hash will disagree, potentially stalling finality.

This matches the allowed impact: **Critical — wrong state/receipt/block hash committed by blockifier/execution logic for accepted input**.

---

### Likelihood Explanation

Every block produced at Starknet version ≥ 0.14.0 triggers the wrong hash. The current codebase targets version 0.14.4. No privileged action is required; the omission fires automatically on every block finalization. The TODO comment confirms the developers are aware the fields must be added, but the gate condition (`after 0.14.0`) has already been crossed.

---

### Recommendation

1. Add `l2_gas_consumed: GasAmount` and `next_l2_gas_price: GasPrice` to `PartialBlockHashComponents`.
2. Populate them in `PartialBlockHashComponents::new` from `BlockInfo` (or pass them explicitly from `FeeMarketInfo`).
3. Introduce a `BlockHashVersion::V0_14_0` (or the appropriate version boundary) and extend `gas_prices_to_hash` to chain `l2_gas_consumed` and `next_l2_gas_price` under that version gate.
4. Update `calculate_block_hash` to pass the new fields through.
5. Add a regression test analogous to `l2_gas_price_pre_v0_13_4` that asserts the hash changes when either new field is modified for `V0_14_x` blocks.

---

### Proof of Concept

**Missing fields in `gas_prices_to_hash`** — the function hashes only six gas-price felts and carries the explicit TODO: [1](#0-0) 

**`PartialBlockHashComponents` has no `l2_gas_consumed` / `next_l2_gas_price` fields**, so they cannot reach the hash even if the function were fixed: [2](#0-1) 

**`BlockHeaderWithoutHash` carries both fields** (they exist in the protocol but are not forwarded to the hash): [3](#0-2) 

**`echonet` already passes both fields to the block-hash CLI**, confirming the protocol requires them: [4](#0-3) 

**`calculate_block_hash` calls `gas_prices_to_hash` and commits the result as the canonical block hash**: [5](#0-4) 

**`finalize_commitment_output` calls `calculate_block_hash`** on every finalised block, writing the wrong hash to storage: [6](#0-5)

### Citations

**File:** crates/starknet_api/src/block_hash/block_hash_calculator.rs (L209-235)
```rust
#[derive(Clone, Debug, Default, PartialEq, Eq, Serialize, Deserialize)]
/// All information required to calculate a block hash except for the state root and the parent
/// block hash.
pub struct PartialBlockHashComponents {
    pub header_commitments: BlockHeaderCommitments,
    pub block_number: BlockNumber,
    pub l1_gas_price: GasPricePerToken,
    pub l1_data_gas_price: GasPricePerToken,
    pub l2_gas_price: GasPricePerToken,
    pub sequencer: SequencerContractAddress,
    pub timestamp: BlockTimestamp,
    pub starknet_version: StarknetVersion,
}

impl PartialBlockHashComponents {
    pub fn new(block_info: &BlockInfo, header_commitments: BlockHeaderCommitments) -> Self {
        Self {
            header_commitments,
            block_number: block_info.block_number,
            l1_gas_price: block_info.gas_prices.l1_gas_price_per_token(),
            l1_data_gas_price: block_info.gas_prices.l1_data_gas_price_per_token(),
            l2_gas_price: block_info.gas_prices.l2_gas_price_per_token(),
            sequencer: SequencerContractAddress(block_info.sequencer_address),
            timestamp: block_info.block_timestamp,
            starknet_version: block_info.starknet_version,
        }
    }
```

**File:** crates/starknet_api/src/block_hash/block_hash_calculator.rs (L245-282)
```rust
pub fn calculate_block_hash(
    partial_block_hash_components: &PartialBlockHashComponents,
    state_root: GlobalRoot,
    previous_block_hash: BlockHash,
) -> StarknetApiResult<BlockHash> {
    let block_hash_version: BlockHashVersion =
        partial_block_hash_components.starknet_version.try_into()?;
    let block_commitments = &partial_block_hash_components.header_commitments;
    Ok(BlockHash(
        HashChain::new()
            .chain(&block_hash_version.clone().into())
            .chain(&partial_block_hash_components.block_number.0.into())
            .chain(&state_root.0)
            .chain(&partial_block_hash_components.sequencer.0)
            .chain(&partial_block_hash_components.timestamp.0.into())
            .chain(&block_commitments.concatenated_counts)
            .chain(&block_commitments.state_diff_commitment.0.0)
            .chain(&block_commitments.transaction_commitment.0)
            .chain(&block_commitments.event_commitment.0)
            .chain(&block_commitments.receipt_commitment.0)
            .chain_iter(
                gas_prices_to_hash(
                    &partial_block_hash_components.l1_gas_price,
                    &partial_block_hash_components.l1_data_gas_price,
                    &partial_block_hash_components.l2_gas_price,
                    &block_hash_version,
                )
                .iter(),
            )
            .chain(
                &Felt::try_from(&partial_block_hash_components.starknet_version)
                    .expect("Expect ASCII version"),
            )
            .chain(&Felt::ZERO)
            .chain(&previous_block_hash.0)
            .get_poseidon_hash(),
    ))
}
```

**File:** crates/starknet_api/src/block_hash/block_hash_calculator.rs (L416-443)
```rust
// TODO(Ayelet): add l2_gas_consumed, next_l2_gas_price after 0.14.0.
pub fn gas_prices_to_hash(
    l1_gas_price: &GasPricePerToken,
    l1_data_gas_price: &GasPricePerToken,
    l2_gas_price: &GasPricePerToken,
    block_hash_version: &BlockHashVersion,
) -> Vec<Felt> {
    if block_hash_version >= &BlockHashVersion::V0_13_4 {
        vec![
            HashChain::new()
                .chain(&STARKNET_GAS_PRICES0)
                .chain(&l1_gas_price.price_in_wei.0.into())
                .chain(&l1_gas_price.price_in_fri.0.into())
                .chain(&l1_data_gas_price.price_in_wei.0.into())
                .chain(&l1_data_gas_price.price_in_fri.0.into())
                .chain(&l2_gas_price.price_in_wei.0.into())
                .chain(&l2_gas_price.price_in_fri.0.into())
                .get_poseidon_hash(),
        ]
    } else {
        vec![
            l1_gas_price.price_in_wei.0.into(),
            l1_gas_price.price_in_fri.0.into(),
            l1_data_gas_price.price_in_wei.0.into(),
            l1_data_gas_price.price_in_fri.0.into(),
        ]
    }
}
```

**File:** crates/starknet_api/src/block.rs (L231-248)
```rust
#[derive(Debug, Default, Clone, Eq, PartialEq, Hash, Deserialize, Serialize, PartialOrd, Ord)]
pub struct BlockHeaderWithoutHash {
    pub parent_hash: BlockHash,
    pub block_number: BlockNumber,
    pub l1_gas_price: GasPricePerToken,
    pub l1_data_gas_price: GasPricePerToken,
    pub l2_gas_price: GasPricePerToken,
    pub l2_gas_consumed: GasAmount,
    pub next_l2_gas_price: GasPrice,
    pub state_root: GlobalRoot,
    pub sequencer: SequencerContractAddress,
    pub timestamp: BlockTimestamp,
    pub l1_da_mode: L1DataAvailabilityMode,
    pub starknet_version: StarknetVersion,
    // TODO(AndrewL): Add this field into the block hash.
    /// Proposer's oracle-derived recommended L2 gas fee. `None` for pre-V0_14_3 blocks.
    pub fee_proposal_fri: Option<GasPrice>,
}
```

**File:** echonet/echo_center.py (L679-682)
```python
                    "l2_gas_price": block_info["l2_gas_price"],
                    "l2_gas_consumed": fee_market_info["l2_gas_consumed"],
                    "next_l2_gas_price": fee_market_info["next_l2_gas_price"],
                    "state_root": state_root,
```

**File:** crates/apollo_batcher/src/commitment_manager/commitment_manager_impl.rs (L561-566)
```rust
                Some(calculate_block_hash(
                    &partial_block_hash_components,
                    global_root,
                    previous_block_hash,
                )?)
            }
```
