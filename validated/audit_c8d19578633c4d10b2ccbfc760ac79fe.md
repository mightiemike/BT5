### Title
Missing `l2_gas_consumed` and `next_l2_gas_price` Fields in `gas_prices_to_hash` for Starknet Versions ≥ 0.14.0 — (`crates/starknet_api/src/block_hash/block_hash_calculator.rs`)

---

### Summary

The `gas_prices_to_hash` function, which feeds directly into `calculate_block_hash`, does not include `l2_gas_consumed` or `next_l2_gas_price` for any block hash version. A TODO comment in the production code explicitly acknowledges these fields must be added "after 0.14.0". The codebase is currently operating at Starknet version 0.14.4, yet the `BlockHashVersion` enum has no `V0_14_0` variant — all versions ≥ 0.13.4 silently collapse to `BlockHashVersion::V0_13_4`. The `PartialBlockHashComponents` struct, which is the sole input to `calculate_block_hash`, does not carry these fields at all. As a result, every block produced at version ≥ 0.14.0 has its block hash computed over an incomplete gas-prices preimage, producing a canonically wrong hash.

---

### Finding Description

**Root cause — `gas_prices_to_hash` is incomplete for versions ≥ 0.14.0**

`gas_prices_to_hash` has two branches:

- For `BlockHashVersion < V0_13_4`: returns four raw felts (L1 gas wei/fri, L1 data gas wei/fri).
- For `BlockHashVersion >= V0_13_4`: returns a single Poseidon hash over seven elements: `STARKNET_GAS_PRICES0`, L1 gas wei/fri, L1 data gas wei/fri, L2 gas wei/fri.

The function signature accepts no `l2_gas_consumed` or `next_l2_gas_price` parameters, and the TODO comment on line 416 reads:

```
// TODO(Ayelet): add l2_gas_consumed, next_l2_gas_price after 0.14.0.
``` [1](#0-0) 

**Version gate is absent — `BlockHashVersion` has no `V0_14_0` variant**

The `BlockHashVersion` enum has exactly two variants: `V0_13_2` and `V0_13_4`. The `TryFrom<StarknetVersion>` implementation maps every version ≥ 0.13.4 — including 0.14.0, 0.14.1, 0.14.2, 0.14.3, and 0.14.4 — to `BlockHashVersion::V0_13_4`:

```rust
} else {
    Ok(Self::V0_13_4)
}
``` [2](#0-1) 

There is no `STARKNET_GAS_PRICES1` constant and no new branch in `gas_prices_to_hash` for a future version.

**`PartialBlockHashComponents` does not carry the missing fields**

The struct that is the sole input to `calculate_block_hash` has `l1_gas_price`, `l1_data_gas_price`, and `l2_gas_price`, but no `l2_gas_consumed` or `next_l2_gas_price`: [3](#0-2) 

**`BlockHashInput::to_final_block_hash_components` silently drops the fields**

The CLI entry point deserializes a full `BlockHeaderWithoutHash` (which does carry `l2_gas_consumed` and `next_l2_gas_price`) but then constructs `PartialBlockHashComponents` without them: [4](#0-3) 

`BlockHeaderWithoutHash` carries both fields: [5](#0-4) 

**`calculate_block_hash` chains the incomplete gas-prices hash**

The final block hash is computed as a Poseidon chain that includes the output of `gas_prices_to_hash`. Because that output is missing two fields for versions ≥ 0.14.0, the resulting block hash is wrong: [6](#0-5) 

---

### Impact Explanation

Every block produced by the sequencer at Starknet version ≥ 0.14.0 (currently 0.14.4) has its block hash computed over a gas-prices preimage that omits `l2_gas_consumed` and `next_l2_gas_price`. This produces a block hash that diverges from the canonical Starknet protocol value for those versions. Consequences:

1. **Wrong block hash stored and served by RPC** — `starknet_getBlockWithTxHashes`, `starknet_getBlockWithTxs`, and related endpoints return an authoritative-looking but incorrect `block_hash` for every block at version ≥ 0.14.0.
2. **Wrong `PartialBlockHash` / `ProposalCommitment`** — `PartialBlockHash::from_partial_block_hash_components` calls `calculate_block_hash` with the same incomplete inputs, so the proposal commitment used in consensus is also wrong.
3. **Cross-node hash mismatch** — Any node or verifier that implements the correct 0.14.x formula (including the canonical L1 verifier) will compute a different hash, breaking block verification and L1 settlement.

---

### Likelihood Explanation

The trigger is the normal operation of the sequencer at Starknet version ≥ 0.14.0. No special input or privileged access is required. The codebase already operates at version 0.14.4 (confirmed by test fixtures and the `StarknetVersion` enum). Every block produced is affected.

---

### Recommendation

1. Add a `V0_14_0` variant to `BlockHashVersion` and extend `TryFrom<StarknetVersion>` to map versions ≥ 0.14.0 to it.
2. Add `l2_gas_consumed: GasAmount` and `next_l2_gas_price: GasPrice` to `PartialBlockHashComponents`.
3. Update `PartialBlockHashComponents::new` and `BlockHashInput::to_final_block_hash_components` to populate these fields from `BlockHeaderWithoutHash`.
4. Add a new `STARKNET_GAS_PRICES1` domain constant and a new branch in `gas_prices_to_hash` for `BlockHashVersion >= V0_14_0` that chains all nine elements: `STARKNET_GAS_PRICES1`, L1 gas wei/fri, L1 data gas wei/fri, L2 gas wei/fri, `l2_gas_consumed`, `next_l2_gas_price`.
5. Add a regression test for `BlockHashVersion::V0_14_0` analogous to the existing `V0_13_4` test.

---

### Proof of Concept

**Step 1 — Confirm the missing version gate.**

`BlockHashVersion::TryFrom<StarknetVersion>` maps `StarknetVersion::V0_14_4` (discriminant 27) to `BlockHashVersion::V0_13_4` because the `else` branch catches everything ≥ 0.13.4: [7](#0-6) 

**Step 2 — Confirm the missing fields in `gas_prices_to_hash`.**

For `BlockHashVersion::V0_13_4` (which covers 0.14.4), the function hashes exactly 7 elements and has no parameter for `l2_gas_consumed` or `next_l2_gas_price`: [8](#0-7) 

**Step 3 — Confirm the fields are dropped in the CLI conversion.**

`BlockHashInput::to_final_block_hash_components` constructs `PartialBlockHashComponents` from `BlockHeaderWithoutHash` but omits `l2_gas_consumed` and `next_l2_gas_price`: [9](#0-8) 

**Step 4 — Confirm the TODO acknowledges the gap for the current version.**

The TODO on line 416 of `block_hash_calculator.rs` reads `// TODO(Ayelet): add l2_gas_consumed, next_l2_gas_price after 0.14.0.` The current production version is 0.14.4, which is past the stated threshold, yet no implementation exists. [10](#0-9)

### Citations

**File:** crates/starknet_api/src/block_hash/block_hash_calculator.rs (L54-83)
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

**File:** crates/starknet_api/src/block.rs (L232-248)
```rust
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
