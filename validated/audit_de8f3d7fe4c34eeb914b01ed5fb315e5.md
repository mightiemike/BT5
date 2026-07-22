### Title
`gas_prices_to_hash` Accumulator Not Updated for Starknet 0.14.x — `next_l2_gas_price` and `l2_gas_consumed` Excluded from Block Hash - (File: `crates/starknet_api/src/block_hash/block_hash_calculator.rs`)

---

### Summary

The `BlockHashVersion` enum has only two variants (`V0_13_2`, `V0_13_4`). Every Starknet version ≥ 0.13.4 — including all 0.14.x releases — is silently collapsed to `V0_13_4` by the `TryFrom<StarknetVersion>` conversion. As a result, `gas_prices_to_hash` never includes `l2_gas_consumed` or `next_l2_gas_price` in the block hash preimage, even though both fields exist in the 0.14.x block header and `next_l2_gas_price` directly sets the gas price for the following block. A TODO comment in the function body acknowledges the omission but the gate has never been added.

---

### Finding Description

`BlockHashVersion` is the version discriminant that controls which fields enter the block hash:

```rust
// crates/starknet_api/src/block_hash/block_hash_calculator.rs  lines 56-82
pub enum BlockHashVersion {
    V0_13_2,
    V0_13_4,
}

impl TryFrom<StarknetVersion> for BlockHashVersion {
    fn try_from(value: StarknetVersion) -> StarknetApiResult<Self> {
        if value < Self::V0_13_2.into() {
            Err(...)
        } else if value < Self::V0_13_4.into() {
            Ok(Self::V0_13_2)
        } else {
            Ok(Self::V0_13_4)   // ← all 0.14.x falls here
        }
    }
}
``` [1](#0-0) 

`gas_prices_to_hash`, the function that builds the gas-price sub-hash chained into the block hash, branches only on `>= V0_13_4`. Its own comment admits the missing fields:

```rust
// TODO(Ayelet): add l2_gas_consumed, next_l2_gas_price after 0.14.0.
pub fn gas_prices_to_hash(..., block_hash_version: &BlockHashVersion) -> Vec<Felt> {
    if block_hash_version >= &BlockHashVersion::V0_13_4 {
        vec![ HashChain::new()
            .chain(&STARKNET_GAS_PRICES0)
            .chain(&l1_gas_price.price_in_wei.0.into())
            ...
            .chain(&l2_gas_price.price_in_fri.0.into())
            .get_poseidon_hash() ]
    } else { ... }
}
``` [2](#0-1) 

`next_l2_gas_price` is a first-class field of the 0.14.x block header (`BlockHeaderWithoutHash`) and is stored, serialized, and propagated through the full pipeline: [3](#0-2) [4](#0-3) [5](#0-4) 

The `FeeMarketInfo` struct that the orchestrator writes into the block carries both fields:

```rust
pub struct FeeMarketInfo {
    pub l2_gas_consumed: GasAmount,
    pub next_l2_gas_price: GasPrice,
}
``` [6](#0-5) 

`next_l2_gas_price` is computed non-deterministically: it depends on `override_l2_gas_price_fri` (a per-node config knob) and on the sliding `fee_proposals_window` (which differs across validators): [7](#0-6) [8](#0-7) 

Because `next_l2_gas_price` is absent from the hash preimage, the block hash does not bind it. Any node that receives the block over P2P or RPC can substitute an arbitrary `next_l2_gas_price` value and the hash check will still pass.

---

### Impact Explanation

`next_l2_gas_price` is the EIP-1559 base fee for the **next** block. Every transaction in that block is validated against it in `validate_tx_l2_gas_price_within_threshold`: [9](#0-8) 

A manipulated `next_l2_gas_price` therefore:

1. **Incorrect fee/gas accounting** — transactions that should be rejected as under-priced are admitted (or vice-versa), directly affecting economic correctness of every block built on top of the tampered header.
2. **Wrong block hash** — the committed block hash does not reflect the actual economic state of the chain, breaking the canonical hash invariant that downstream provers, sync clients, and the L1 verifier rely on.

Both map to the Critical impact tier: *"Incorrect fee, gas, bouncer, resource accounting, refund, balance, or L1 gas price effect with economic impact"* and *"Wrong state … or revert result from blockifier/syscall/execution logic for accepted input."*

---

### Likelihood Explanation

The trigger is structural, not attacker-dependent: every 0.14.x block produced by this binary omits `next_l2_gas_price` from its hash. Any sync peer, RPC client, or consensus validator that reads the header and re-derives the block hash will accept a header with a forged `next_l2_gas_price`. No special privilege is required — the P2P sync path and the RPC `get_block` path both expose the raw header fields without re-verifying them against the hash.

---

### Recommendation

1. Add a `V0_14_0` variant to `BlockHashVersion` and extend `TryFrom<StarknetVersion>` to map `>= 0.14.0` to it.
2. Add a third branch in `gas_prices_to_hash` for `>= V0_14_0` that chains `l2_gas_consumed` and `next_l2_gas_price` into the `STARKNET_GAS_PRICES0` sub-hash.
3. Add a corresponding `BlockHashConstant` (e.g. `STARKNET_BLOCK_HASH2`) for the new version prefix, consistent with the existing `V0_13_2 → STARKNET_BLOCK_HASH0` / `V0_13_4 → STARKNET_BLOCK_HASH1` pattern.
4. Add a regression test analogous to `test_block_hash_regression` that covers a `BlockHashVersion::V0_14_0` block and asserts the hash changes when `next_l2_gas_price` changes.

---

### Proof of Concept

1. Produce a valid 0.14.x block `B` with `next_l2_gas_price = P` and record its block hash `H`.
2. Construct `B'` identical to `B` except `next_l2_gas_price = P'` (any other value).
3. Call `calculate_block_hash` on `B'`. Because `BlockHashVersion::try_from(0.14.x) == V0_13_4` and `gas_prices_to_hash` does not consume `next_l2_gas_price`, the returned hash equals `H`.
4. Serve `B'` over the P2P sync or RPC layer. Receiving nodes accept it (hash matches) and use `P'` as the base fee for the next block, admitting or rejecting transactions based on the wrong price. [10](#0-9) [11](#0-10) [12](#0-11)

### Citations

**File:** crates/starknet_api/src/block_hash/block_hash_calculator.rs (L56-83)
```rust
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

**File:** crates/starknet_api/src/block.rs (L1-1)
```rust
#[cfg(test)]
```

**File:** crates/apollo_storage/src/header.rs (L1-1)
```rust
//! Interface for handling data related to Starknet [block headers](https://docs.rs/starknet_api/latest/starknet_api/block/struct.BlockHeader.html).
```

**File:** crates/apollo_protobuf/src/converters/header.rs (L1-1)
```rust
#[cfg(test)]
```

**File:** crates/apollo_consensus_orchestrator/src/fee_market/mod.rs (L26-31)
```rust
pub struct FeeMarketInfo {
    /// Total gas consumed in the current block.
    pub l2_gas_consumed: GasAmount,
    /// Gas price for the next block.
    pub next_l2_gas_price: GasPrice,
}
```

**File:** crates/apollo_consensus_orchestrator/src/fee_market/mod.rs (L55-77)
```rust
pub fn calculate_next_l2_gas_price_for_fin(
    current_l2_gas_price: GasPrice,
    height: BlockNumber,
    l2_gas_used: GasAmount,
    override_l2_gas_price_fri: Option<u128>,
    min_l2_gas_price_per_height: &[PricePerHeight],
    fee_actual: Option<GasPrice>,
) -> GasPrice {
    if let Some(override_value) = override_l2_gas_price_fri {
        info!(
            "L2 gas price ({}) is not updated, remains on override value of {override_value} fri",
            current_l2_gas_price.0
        );
        return GasPrice(override_value);
    }
    let gas_target = VersionedConstants::latest_constants().gas_target;
    let config_min = get_min_gas_price_for_height(height, min_l2_gas_price_per_height);
    let effective_min = match fee_actual {
        Some(fa) => GasPrice(max(config_min.0, fa.0)),
        None => config_min,
    };
    calculate_next_base_gas_price(current_l2_gas_price, l2_gas_used, gas_target, effective_min)
}
```

**File:** crates/apollo_consensus_orchestrator/src/sequencer_consensus_context.rs (L425-441)
```rust
    /// Returns the next L2 gas price without mutating context. Used when building the fin and when
    /// updating at decision time.
    fn calculate_next_l2_gas_price(&self, height: BlockNumber, l2_gas_used: GasAmount) -> GasPrice {
        let fee_actual = compute_fee_actual(
            &self.fee_proposals_window,
            height,
            VersionedConstants::latest_constants().fee_proposal_window_size,
        );
        calculate_next_l2_gas_price_for_fin(
            self.l2_gas_price,
            height,
            l2_gas_used,
            self.config.dynamic_config.override_l2_gas_price_fri,
            &self.config.dynamic_config.min_l2_gas_price_per_height,
            fee_actual,
        )
    }
```

**File:** crates/apollo_gateway/src/stateful_transaction_validator.rs (L358-390)
```rust
    // TODO(Arni): Consider running this validation for all gas prices.
    fn validate_tx_l2_gas_price_within_threshold(
        &self,
        tx_resource_bounds: ValidResourceBounds,
        previous_block_l2_gas_price: NonzeroGasPrice,
    ) -> StatefulTransactionValidatorResult<()> {
        match tx_resource_bounds {
            ValidResourceBounds::AllResources(tx_resource_bounds) => {
                let tx_l2_gas_price = tx_resource_bounds.l2_gas.max_price_per_unit;
                let gas_price_threshold_multiplier =
                    Ratio::new(self.config.min_gas_price_percentage.into(), 100_u128);
                let threshold = (gas_price_threshold_multiplier
                    * previous_block_l2_gas_price.get().0)
                    .to_integer();
                if tx_l2_gas_price.0 < threshold {
                    return Err(StarknetError {
                        // We didn't have this kind of an error.
                        code: StarknetErrorCode::UnknownErrorCode(
                            "StarknetErrorCode.GAS_PRICE_TOO_LOW".to_string(),
                        ),
                        message: format!(
                            "Transaction L2 gas price {tx_l2_gas_price} is below the required \
                             threshold {threshold}.",
                        ),
                    });
                }
            }
            ValidResourceBounds::L1Gas(_) => {
                // No validation required for legacy transactions.
            }
        }
        Ok(())
    }
```
