### Title
`gas_prices_to_hash` Version Gate Off-by-One Omits L2 Gas Prices from Block Hash for Starknet Version 0.13.3 Blocks — (`File: crates/starknet_api/src/block_hash/block_hash_calculator.rs`)

---

### Summary

The `gas_prices_to_hash` function contains a version gate that determines whether L2 gas prices are included in the block hash. The function's own comment states the new format (including `l2_gas_price_wei` and `l2_gas_price_fri`) applies from **Starknet version ≥ 0.13.3**, but the code gates on `BlockHashVersion::V0_13_4`. For any block at Starknet version 0.13.3, the code falls into the `else` branch and emits only the four L1 gas price felts, silently omitting both L2 gas price components from the block hash preimage.

---

### Finding Description

`gas_prices_to_hash` in `block_hash_calculator.rs` is the single function that serializes all gas prices into the block hash chain. Its comment reads:

```
// For starknet version >= 0.13.3, returns:
// [Poseidon (
//     "STARKNET_GAS_PRICES0", gas_price_wei, gas_price_fri, data_gas_price_wei, data_gas_price_fri,
//     l2_gas_price_wei, l2_gas_price_fri
// )].
// Otherwise, returns:
// [gas_price_wei, gas_price_fri, data_gas_price_wei, data_gas_price_fri].
```

But the actual branch condition is:

```rust
if block_hash_version >= &BlockHashVersion::V0_13_4 {
```

For a block whose `starknet_version` maps to `BlockHashVersion::V0_13_3`, the condition is `false`. The function returns the four-felt legacy vector:

```rust
vec![
    l1_gas_price.price_in_wei.0.into(),
    l1_gas_price.price_in_fri.0.into(),
    l1_data_gas_price.price_in_wei.0.into(),
    l1_data_gas_price.price_in_fri.0.into(),
]
```

`l2_gas_price.price_in_wei` and `l2_gas_price.price_in_fri` are never chained. The Poseidon domain tag `STARKNET_GAS_PRICES0` is also absent. The resulting single-felt contribution to `calculate_block_hash` is therefore wrong for every 0.13.3 block.

`calculate_block_hash` chains this output directly into the Poseidon block hash:

```rust
.chain_iter(
    gas_prices_to_hash(
        &partial_block_hash_components.l1_gas_price,
        &partial_block_hash_components.l1_data_gas_price,
        &partial_block_hash_components.l2_gas_price,
        &block_hash_version,
    )
    .iter(),
)
```

The `l2_gas_price` argument is populated from `BlockInfo::gas_prices.l2_gas_price_per_token()`, which carries a real non-zero value for every block since 0.13.3. That value is simply never hashed.

---

### Impact Explanation

The block hash is the canonical identifier committed to L1 and used by every validator to agree on a block. A wrong block hash for version 0.13.3 blocks means:

- **Wrong block hash stored and served by the node** — any RPC call returning the block hash for a 0.13.3 block returns an incorrect value.
- **Wrong value committed to L1** — the state update sent to the Starknet core contract carries the incorrect hash, permanently mis-anchoring that block on Ethereum.
- **Consensus / sync divergence** — a node that correctly implements the 0.13.3 spec (including L2 gas prices in the hash) would compute a different hash than this implementation, causing a canonicalization split.

This matches the Critical impact: *"Wrong state, receipt, event, L1 message, class hash, storage value, or revert result from blockifier/syscall/execution logic for accepted input."*

---

### Likelihood Explanation

Starknet version 0.13.3 is a real, deployed protocol version. Any node syncing historical blocks at that version, or any node that was live during the 0.13.3 epoch, would compute wrong block hashes for that range. The trigger requires no special privilege — it fires automatically for every block whose `starknet_version` maps to `BlockHashVersion::V0_13_3`.

---

### Recommendation

Change the version gate to match the documented intent:

```rust
// Before:
if block_hash_version >= &BlockHashVersion::V0_13_4 {

// After:
if block_hash_version >= &BlockHashVersion::V0_13_3 {
```

If the intent is genuinely 0.13.4, update the comment to say `>= 0.13.4` and add a test that explicitly asserts the 0.13.3 format omits L2 gas prices.

---

### Proof of Concept

Given a `PartialBlockHashComponents` for a block at `starknet_version = "0.13.3"` with non-zero `l2_gas_price`:

1. `calculate_block_hash` calls `gas_prices_to_hash(..., &BlockHashVersion::V0_13_3)`.
2. `V0_13_3 >= V0_13_4` is `false`; the `else` branch executes.
3. The returned `Vec<Felt>` has **4 elements** (L1 prices only); `l2_gas_price.price_in_wei` and `l2_gas_price.price_in_fri` are absent.
4. The Poseidon chain receives 4 felts instead of the single Poseidon-compressed felt that includes all 6 gas price components.
5. The resulting `BlockHash` differs from the hash a spec-compliant implementation would produce for the same block. [1](#0-0) [2](#0-1) [3](#0-2)

### Citations

**File:** crates/starknet_api/src/block_hash/block_hash_calculator.rs (L223-235)
```rust
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
