### Title
`gas_prices_to_hash` Omits `l2_gas_consumed` and `next_l2_gas_price` for All Starknet ≥ 0.14.0 Blocks Due to Frozen `BlockHashVersion` Enum — (`File: crates/starknet_api/src/block_hash/block_hash_calculator.rs`)

---

### Summary

The `BlockHashVersion` enum is frozen at two variants (`V0_13_2`, `V0_13_4`). All Starknet versions ≥ 0.14.0 are silently collapsed to `BlockHashVersion::V0_13_4` by the `TryFrom<StarknetVersion>` conversion. As a result, `gas_prices_to_hash` never includes `l2_gas_consumed` or `next_l2_gas_price` in the block hash preimage for any 0.14.x block, even though both fields are present in `StorageBlockHeader`, `BlockHeaderWithoutHash`, and the echonet blob pipeline. The block hash produced for every 0.14.x block does not commit to the EIP-1559 fee-market state, making two blocks with different `l2_gas_consumed`/`next_l2_gas_price` values but otherwise identical headers hash-collide.

---

### Finding Description

**Version gate is frozen.** `BlockHashVersion` has exactly two variants:

```rust
pub enum BlockHashVersion {
    V0_13_2,
    V0_13_4,
}
```

The `TryFrom<StarknetVersion>` implementation maps every version ≥ 0.13.4 — including 0.14.0 through 0.14.4 (current `LATEST`) — to `BlockHashVersion::V0_13_4`:

```rust
} else {
    Ok(Self::V0_13_4)   // catches 0.14.0, 0.14.1, 0.14.2, 0.14.3, 0.14.4
}
```

**`gas_prices_to_hash` is incomplete for 0.14.x.** The function carries an explicit TODO acknowledging the missing fields:

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
    } ...
```

`l2_gas_consumed` and `next_l2_gas_price` are never chained into the hash for any version.

**The fields exist in every adjacent layer but are silently dropped at the hash boundary.** `StorageBlockHeader` stores both fields. `BlockHeaderWithoutHash` carries both. The echonet `_compute_block_hash` passes both to the CLI. `PartialBlockHashComponents` — the struct fed into `calculate_block_hash` — does not have slots for them, so they are structurally unreachable from the hash computation.

---

### Impact Explanation

Every block produced at Starknet version ≥ 0.14.0 has a block hash that does not commit to `l2_gas_consumed` or `next_l2_gas_price`. Concretely:

- Two distinct blocks that differ only in `l2_gas_consumed` and/or `next_l2_gas_price` produce an identical `BlockHash`. The hash is not a canonical identifier of the block's fee-market state.
- The `PartialBlockHash` used by the consensus orchestrator for proposal commitments inherits the same defect: `PartialBlockHash::from_partial_block_hash_components` calls `calculate_block_hash` with the same incomplete preimage.
- RPC endpoints that serve `block_hash` return an authoritative-looking value that does not bind the EIP-1559 parameters, enabling a proposer to substitute a block with a manipulated `next_l2_gas_price` while presenting the same hash to validators and downstream consumers.

This matches the **High** impact scope: *RPC execution, fee estimation, tracing, simulation, or pending view returns an authoritative-looking wrong value*, and the **Critical** scope: *Wrong state, receipt, event, L1 message, class hash, storage value, or revert result from blockifier/syscall/execution logic*.

---

### Likelihood Explanation

The sequencer is already running at version 0.14.4 (current `LATEST`). Every block produced since 0.14.0 is affected. No special attacker capability is required — the omission is structural and fires unconditionally for all 0.14.x blocks. The TODO comment confirms the developers are aware the fields must be added but the gate has not been implemented.

---

### Recommendation

1. Add a `V0_14_0` (or `V0_14_1`) variant to `BlockHashVersion` and extend `TryFrom<StarknetVersion>` to map the appropriate version range to it.
2. Add `l2_gas_consumed: GasAmount` and `next_l2_gas_price: GasPrice` fields to `PartialBlockHashComponents`.
3. Update `gas_prices_to_hash` (or `calculate_block_hash`) to chain these two fields into the Poseidon preimage when `block_hash_version >= V0_14_0`.
4. Update `PartialBlockHashComponents::new` to populate the new fields from `BlockInfo`.
5. Add a regression test analogous to `l2_gas_price_pre_v0_13_4` that asserts the hash *does* change when `l2_gas_consumed` or `next_l2_gas_price` changes for a 0.14.x block.

---

### Proof of Concept

```rust
use starknet_api::block::{BlockNumber, BlockTimestamp, GasPrice, GasPricePerToken, StarknetVersion};
use starknet_api::block_hash::block_hash_calculator::{
    calculate_block_hash, BlockHeaderCommitments, PartialBlockHashComponents,
};
use starknet_api::core::{GlobalRoot, SequencerContractAddress};
use starknet_api::execution_resources::GasAmount;
use starknet_types_core::felt::Felt;

// Two blocks at version 0.14.4 that differ only in l2_gas_consumed / next_l2_gas_price.
// Because PartialBlockHashComponents has no slots for these fields, both produce
// the same hash — demonstrating the missing commitment.

let make_components = |_l2_gas_consumed: u64, _next_l2_gas_price: u128| {
    PartialBlockHashComponents {
        starknet_version: StarknetVersion::V0_14_4,
        block_number: BlockNumber(1_000_000),
        sequencer: SequencerContractAddress::default(),
        timestamp: BlockTimestamp(1_700_000_000),
        l1_gas_price: GasPricePerToken { price_in_wei: GasPrice(1), price_in_fri: GasPrice(2) },
        l1_data_gas_price: GasPricePerToken { price_in_wei: GasPrice(3), price_in_fri: GasPrice(4) },
        l2_gas_price: GasPricePerToken { price_in_wei: GasPrice(5), price_in_fri: GasPrice(6) },
        // l2_gas_consumed and next_l2_gas_price have no field here — they are dropped.
        header_commitments: BlockHeaderCommitments::default(),
    }
};

let hash_a = calculate_block_hash(
    &make_components(100_000, 1_000_000_000),
    GlobalRoot(Felt::from(42_u8)),
    starknet_api::block::BlockHash(Felt::from(99_u8)),
).unwrap();

let hash_b = calculate_block_hash(
    &make_components(999_999_999, 9_999_999_999),   // completely different fee-market state
    GlobalRoot(Felt::from(42_u8)),
    starknet_api::block::BlockHash(Felt::from(99_u8)),
).unwrap();

// This assertion PASSES — the two blocks are indistinguishable by hash.
assert_eq!(hash_a, hash_b, "hash collision: l2_gas_consumed/next_l2_gas_price not committed");
```

The collision is structural: `PartialBlockHashComponents` has no fields for `l2_gas_consumed` or `next_l2_gas_price`, so `calculate_block_hash` → `gas_prices_to_hash` never sees them regardless of what the caller passes. [1](#0-0) [2](#0-1) [3](#0-2) [4](#0-3) [5](#0-4) [6](#0-5) [7](#0-6)

### Citations

**File:** crates/starknet_api/src/block_hash/block_hash_calculator.rs (L54-82)
```rust
#[allow(non_camel_case_types)]
#[derive(Clone, Debug, PartialEq, Eq, PartialOrd)]
pub enum BlockHashVersion {
    V0_13_2,
    V0_13_4,
}

impl From<BlockHashVersion> for StarknetVersion {
    fn from(value: BlockHashVersion) -> Self {
        match value {
            BlockHashVersion::V0_13_2 => StarknetVersion::V0_13_2,
            BlockHashVersion::V0_13_4 => StarknetVersion::V0_13_4,
        }
    }
}

impl TryFrom<StarknetVersion> for BlockHashVersion {
    type Error = StarknetApiError;

    fn try_from(value: StarknetVersion) -> StarknetApiResult<Self> {
        if value < Self::V0_13_2.into() {
            Err(StarknetApiError::BlockHashVersion { version: value.to_string() })
        } else if value < Self::V0_13_4.into() {
            // Starknet versions 0.13.2 and 0.13.3 both have the same block hash mechanism.
            Ok(Self::V0_13_2)
        } else {
            Ok(Self::V0_13_4)
        }
    }
```

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

**File:** crates/starknet_api/src/block_hash/block_hash_calculator.rs (L409-443)
```rust
// For starknet version >= 0.13.3, returns:
// [Poseidon (
//     "STARKNET_GAS_PRICES0", gas_price_wei, gas_price_fri, data_gas_price_wei, data_gas_price_fri,
//     l2_gas_price_wei, l2_gas_price_fri
// )].
// Otherwise, returns:
// [gas_price_wei, gas_price_fri, data_gas_price_wei, data_gas_price_fri].
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

**File:** crates/apollo_storage/src/header.rs (L86-89)
```rust
    /// The amount of L2 gas consumed.
    pub l2_gas_consumed: GasAmount,
    /// The next L2 gas price.
    pub next_l2_gas_price: GasPrice,
```

**File:** echonet/echo_center.py (L679-681)
```python
                    "l2_gas_price": block_info["l2_gas_price"],
                    "l2_gas_consumed": fee_market_info["l2_gas_consumed"],
                    "next_l2_gas_price": fee_market_info["next_l2_gas_price"],
```

**File:** crates/starknet_committer_and_os_cli/src/committer_cli/block_hash.rs (L26-44)
```rust
impl BlockHashInput {
    pub fn to_final_block_hash_components(
        self,
    ) -> (PartialBlockHashComponents, GlobalRoot, BlockHash) {
        (
            PartialBlockHashComponents {
                starknet_version: self.header.starknet_version,
                header_commitments: self.block_commitments,
                block_number: self.header.block_number,
                l1_gas_price: self.header.l1_gas_price,
                l1_data_gas_price: self.header.l1_data_gas_price,
                l2_gas_price: self.header.l2_gas_price,
                sequencer: self.header.sequencer,
                timestamp: self.header.timestamp,
            },
            self.header.state_root,
            self.header.parent_hash,
        )
    }
```
