### Title
`gas_prices_to_hash` omits `l2_gas_consumed` and `next_l2_gas_price` from block hash preimage, producing wrong block hash for Starknet ≥ 0.14.0 — (`File: crates/starknet_api/src/block_hash/block_hash_calculator.rs`)

---

### Summary

`gas_prices_to_hash` computes the gas-prices sub-hash that is chained into every block hash. For Starknet versions ≥ 0.13.4 it hashes six fields (`l1_gas_price_wei/fri`, `l1_data_gas_price_wei/fri`, `l2_gas_price_wei/fri`), but silently drops `l2_gas_consumed` and `next_l2_gas_price` — two fields that live in the canonical block header and that a developer TODO explicitly marks as required additions "after 0.14.0". The current production version in the test corpus is 0.14.4. The omission means the block hash does not commit to the actual gas consumed in the block nor to the EIP-1559-derived price for the next block, so both values can diverge from what was executed without invalidating the hash.

---

### Finding Description

`gas_prices_to_hash` is the sole function that converts gas-price data into the felt(s) chained into `calculate_block_hash`. Its implementation for the current version gate (`>= V0_13_4`) is:

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
}
``` [1](#0-0) 

The canonical block header type `BlockHeaderWithoutHash` carries both missing fields:

```rust
pub l2_gas_consumed: GasAmount,
pub next_l2_gas_price: GasPrice,
``` [2](#0-1) 

`StorageBlockHeader` mirrors them: [3](#0-2) 

`PartialBlockHashComponents`, the struct that feeds `calculate_block_hash`, has no `l2_gas_consumed` or `next_l2_gas_price` fields and its constructor `PartialBlockHashComponents::new` never reads them from `BlockInfo`: [4](#0-3) 

`calculate_block_hash` therefore chains the output of `gas_prices_to_hash` — which never receives these two values — directly into the Poseidon hash that becomes the canonical block hash: [5](#0-4) 

The echonet replay tool (`_compute_block_hash`) passes `l2_gas_consumed` and `next_l2_gas_price` to the block-hash CLI, confirming the protocol intent that they belong in the hash: [6](#0-5) 

The test corpus already uses `starknet_version: "0.14.4"`, past the "after 0.14.0" threshold in the TODO: [7](#0-6) 

---

### Impact Explanation

Because `l2_gas_consumed` and `next_l2_gas_price` are absent from the hash preimage, two blocks that differ only in those two fields produce an identical block hash. Concretely:

1. **Wrong block hash committed to L1.** The hash that the sequencer commits on-chain does not bind the actual gas consumed in the block. The L1 verifier cannot distinguish a block that consumed 1 gas unit from one that consumed the entire block limit.

2. **`next_l2_gas_price` is unbound.** `next_l2_gas_price` is the EIP-1559-derived price used for all fee calculations in the immediately following block. Because it is not covered by the block hash, a proposer can publish any value for it without invalidating the hash. This directly affects the fee charged to every transaction in the next block — an economic impact matching the "Incorrect fee, gas … with economic impact" criterion.

3. **Consensus `PartialBlockHash` is also wrong.** `PartialBlockHash::from_partial_block_hash_components` calls the same `calculate_block_hash` path, so the commitment exchanged during consensus also omits these fields, meaning validators cannot detect a manipulated `next_l2_gas_price` through the hash. [8](#0-7) 

---

### Likelihood Explanation

The TODO comment names the threshold as "after 0.14.0". The production codebase already processes blocks at version 0.14.4. Every block produced since 0.14.0 has been hashed without these fields. The omission is structural (the fields are absent from `PartialBlockHashComponents`), not gated by any runtime flag, so it fires on every block unconditionally.

---

### Recommendation

1. Add `l2_gas_consumed: GasAmount` and `next_l2_gas_price: GasPrice` to `PartialBlockHashComponents`.
2. Populate them in `PartialBlockHashComponents::new` from the block header (they are available in `BlockHeaderWithoutHash` / `StorageBlockHeader`).
3. Thread them into `gas_prices_to_hash` (or a new `gas_prices_and_fee_market_to_hash`) and chain the result into `calculate_block_hash` under a version gate `>= BlockHashVersion::V0_14_0` (or whichever version the protocol specifies).
4. Update the `PartialBlockHash` consensus commitment path identically so proposer and validator agree on the new preimage.

---

### Proof of Concept

```
Block A: l2_gas_consumed = 1_000_000,  next_l2_gas_price = 1_000_000_000
Block B: l2_gas_consumed = 10_000_000, next_l2_gas_price = 2_000_000_000

All other fields identical.

calculate_block_hash(A) == calculate_block_hash(B)   // same hash, different state
```

`gas_prices_to_hash` receives only `l1_gas_price`, `l1_data_gas_price`, `l2_gas_price` — none of which differ between A and B — so the Poseidon sub-hash is identical, and the final block hash is identical. A proposer can freely choose any `next_l2_gas_price` for the next block without the hash changing, directly controlling the fee baseline for all subsequent transactions. [9](#0-8)

### Citations

**File:** crates/starknet_api/src/block_hash/block_hash_calculator.rs (L185-206)
```rust
/// Hash of [`PartialBlockHashComponents`] only (no state root or parent hash).
#[derive(Clone, Copy, Debug, Default, PartialEq, Eq, Hash, Serialize, Deserialize)]
pub struct PartialBlockHash(pub StarkHash);

impl PartialBlockHash {
    // TODO(Ariel): Use parent_partial_block_hash instead of zero.
    const GLOBAL_ROOT_FOR_PARTIAL_BLOCK_HASH: GlobalRoot = GlobalRoot(Felt::ZERO);
    const PARENT_HASH_FOR_PARTIAL_BLOCK_HASH: BlockHash = BlockHash(Felt::ZERO);

    /// Hash of [`PartialBlockHashComponents`].
    /// Uses the same formula as [`calculate_block_hash`] with the fixed constants above for the
    /// state root and parent hash.
    pub fn from_partial_block_hash_components(
        partial_block_hash_components: &PartialBlockHashComponents,
    ) -> StarknetApiResult<Self> {
        let block_hash = calculate_block_hash(
            partial_block_hash_components,
            Self::GLOBAL_ROOT_FOR_PARTIAL_BLOCK_HASH,
            Self::PARENT_HASH_FOR_PARTIAL_BLOCK_HASH,
        )?;
        Ok(Self(block_hash.0))
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

**File:** crates/starknet_api/src/block.rs (L238-239)
```rust
    pub l2_gas_consumed: GasAmount,
    pub next_l2_gas_price: GasPrice,
```

**File:** crates/apollo_storage/src/header.rs (L87-89)
```rust
    pub l2_gas_consumed: GasAmount,
    /// The next L2 gas price.
    pub next_l2_gas_price: GasPrice,
```

**File:** echonet/echo_center.py (L661-691)
```python
    def _compute_block_hash(
        self,
        blob: JsonObject,
        block_number: int,
        parent_block_hash: str,
        state_root: str,
        block_commitments: JsonObject,
    ) -> str:
        block_info = blob["state_diff"]["block_info"]
        fee_market_info = blob["fee_market_info"]
        result = self._run_block_hash_cli(
            "block-hash",
            {
                "header": {
                    "parent_hash": parent_block_hash,
                    "block_number": block_number,
                    "l1_gas_price": block_info["l1_gas_price"],
                    "l1_data_gas_price": block_info["l1_data_gas_price"],
                    "l2_gas_price": block_info["l2_gas_price"],
                    "l2_gas_consumed": fee_market_info["l2_gas_consumed"],
                    "next_l2_gas_price": fee_market_info["next_l2_gas_price"],
                    "state_root": state_root,
                    "sequencer": block_info["sequencer_address"],
                    "timestamp": int(block_info["block_timestamp"]),
                    "l1_da_mode": "BLOB" if block_info.get("use_kzg_da") else "CALLDATA",
                    "starknet_version": block_info["starknet_version"],
                },
                "block_commitments": block_commitments,
            },
        )
        return str(result)
```

**File:** crates/apollo_starknet_client/resources/reader/block_post_0_14_2.json (L1188-1191)
```json
    "starknet_version": "0.14.4",
    "l2_gas_consumed": 988191555,
    "next_l2_gas_price": "0x1dcd65000"
}
```
