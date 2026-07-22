### Title
`gas_prices_to_hash` Omits `l2_gas_consumed` and `next_l2_gas_price` from Block Hash for Starknet Versions ≥ 0.14.0 — (`File: crates/starknet_api/src/block_hash/block_hash_calculator.rs`)

### Summary

`gas_prices_to_hash` computes the gas-prices sub-hash that is folded into every block hash. For all `BlockHashVersion::V0_13_4` blocks (which covers every Starknet version ≥ 0.13.4, including the entire 0.14.x series), the function hashes only the six price fields (`l1_gas_price_{wei,fri}`, `l1_data_gas_price_{wei,fri}`, `l2_gas_price_{wei,fri}`). The two fee-market fields `l2_gas_consumed` and `next_l2_gas_price` — which are present in the block header, stored in the DB, and passed to the block-hash CLI by `echonet` — are silently excluded. A self-contained TODO comment in the same function acknowledges the omission.

### Finding Description

`gas_prices_to_hash` is the single function that converts gas-price data into the felt(s) chained into `calculate_block_hash`. Its current implementation for `BlockHashVersion::V0_13_4`:

```rust
// TODO(Ayelet): add l2_gas_consumed, next_l2_gas_price after 0.14.0.
pub fn gas_prices_to_hash(...) -> Vec<Felt> {
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
}
``` [1](#0-0) 

The `BlockHashVersion` enum has only two variants (`V0_13_2`, `V0_13_4`); every Starknet version ≥ 0.13.4 — including 0.14.0 through 0.14.4 (the current `LATEST`) — maps to `V0_13_4` with no further version gate: [2](#0-1) 

Meanwhile, `l2_gas_consumed` and `next_l2_gas_price` are canonical block-header fields stored in `BlockHeaderWithoutHash` and `StorageBlockHeader`: [3](#0-2) [4](#0-3) 

The `echonet` block-hash computation explicitly passes both fields to the CLI: [5](#0-4) 

The Rust `gas_prices_to_hash` function signature does not even accept `l2_gas_consumed` or `next_l2_gas_price` as parameters, so they cannot be included regardless of version: [6](#0-5) 

`PartialBlockHashComponents`, the struct that feeds `calculate_block_hash`, also lacks these two fields: [7](#0-6) 

### Impact Explanation

The block hash is the canonical identifier of a Starknet block. It is:
- stored in the DB and returned by the `get_block_hash` syscall to executing contracts;
- verified by the L1 core contract;
- used by the sync layer to authenticate headers received from peers.

Because `l2_gas_consumed` and `next_l2_gas_price` are excluded from the hash preimage for all 0.14.x blocks, two distinct blocks that differ only in these fee-market fields produce an identical block hash. A sequencer can therefore commit a block with one `next_l2_gas_price` value while the hash attests to a different one, or the `get_block_hash` syscall returns a hash that does not bind to the fee-market state of the block. This is a wrong hash value returned from execution logic for accepted input, and it breaks the binding between the block hash and the full block header.

### Likelihood Explanation

The omission is present in the production code path for every block with `starknet_version ≥ 0.14.0`. The TODO comment confirms the fields are known to be missing. Any block produced or synced under 0.14.x is affected. No special attacker capability is required; the divergence is structural.

### Recommendation

1. Add `l2_gas_consumed: GasAmount` and `next_l2_gas_price: GasPrice` to `PartialBlockHashComponents`.
2. Add a new `BlockHashVersion::V0_14_x` variant (or the appropriate version boundary) and extend `gas_prices_to_hash` to chain these two fields into the Poseidon hash for that version.
3. Update `PartialBlockHashComponents::new` to populate the new fields from `BlockInfo` (or from the fee-market info passed alongside it).
4. Update the regression test vectors in `block_hash_calculator_test.rs` to cover the new version.

### Proof of Concept

For any two 0.14.x blocks `B1` and `B2` that are identical except `B1.l2_gas_consumed = X` and `B2.l2_gas_consumed = Y` (X ≠ Y):

```
calculate_block_hash(B1_components, root, parent)
  == calculate_block_hash(B2_components, root, parent)
```

because `gas_prices_to_hash` receives neither value and produces the same single felt for both. The `get_block_hash` syscall executed inside a contract on block `B1` returns a hash that is equally valid for `B2`, breaking the one-to-one mapping between block hash and block content for the fee-market fields. [8](#0-7)

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

**File:** crates/starknet_api/src/block_hash/block_hash_calculator.rs (L209-221)
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

**File:** crates/starknet_api/src/block.rs (L237-240)
```rust
    pub l2_gas_price: GasPricePerToken,
    pub l2_gas_consumed: GasAmount,
    pub next_l2_gas_price: GasPrice,
    pub state_root: GlobalRoot,
```

**File:** crates/apollo_storage/src/header.rs (L86-90)
```rust
    /// The amount of L2 gas consumed.
    pub l2_gas_consumed: GasAmount,
    /// The next L2 gas price.
    pub next_l2_gas_price: GasPrice,
    /// The state root after this block.
```

**File:** echonet/echo_center.py (L679-682)
```python
                    "l2_gas_price": block_info["l2_gas_price"],
                    "l2_gas_consumed": fee_market_info["l2_gas_consumed"],
                    "next_l2_gas_price": fee_market_info["next_l2_gas_price"],
                    "state_root": state_root,
```
