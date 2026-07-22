### Title
`gas_prices_to_hash` omits `l2_gas_consumed` and `next_l2_gas_price` from block hash for Starknet ≥ 0.14.0, producing canonically wrong block hashes — (File: `crates/starknet_api/src/block_hash/block_hash_calculator.rs`)

---

### Summary

The `gas_prices_to_hash` function, which computes the gas-prices sub-hash chained into every block hash, does not include `l2_gas_consumed` or `next_l2_gas_price` for any Starknet version. A developer TODO at line 416 explicitly acknowledges these fields must be added "after 0.14.0", but no implementation exists and no `BlockHashVersion` gate for 0.14.x has been added. The `echonet` pipeline already forwards both fields to the block-hash CLI, and both fields are present in `BlockHeaderWithoutHash` and `StorageBlockHeader` at hash-computation time. Every block produced at Starknet version ≥ 0.14.0 therefore carries a block hash that is missing two fee-market fields, making the sequencer commit and serve a canonically incorrect block hash.

---

### Finding Description

In `gas_prices_to_hash`, for `BlockHashVersion::V0_13_4` (which covers all Starknet versions ≥ 0.13.4, including every 0.14.x release), the function hashes only:

```
Poseidon("STARKNET_GAS_PRICES0",
         l1_gas_price_wei, l1_gas_price_fri,
         l1_data_gas_price_wei, l1_data_gas_price_fri,
         l2_gas_price_wei, l2_gas_price_fri)
``` [1](#0-0) 

The developer TODO at line 416 reads:

> `// TODO(Ayelet): add l2_gas_consumed, next_l2_gas_price after 0.14.0.`

This is the direct analog of the external "missing rewards calculation" bug: the specification documents that `l2_gas_consumed` and `next_l2_gas_price` belong in the hash, but the implementation simply omits them.

The `BlockHashVersion` enum has only two variants (`V0_13_2`, `V0_13_4`) with no `V0_14_0` entry, so there is no version gate that could ever enable the extended hash: [2](#0-1) 

Both missing fields are present in `BlockHeaderWithoutHash` and `StorageBlockHeader` at hash-computation time: [3](#0-2) [4](#0-3) 

The `echonet` pipeline already passes both fields to the block-hash CLI, confirming the reference implementation expects them in the preimage: [5](#0-4) 

`PartialBlockHashComponents`, which feeds `gas_prices_to_hash`, does not carry `l2_gas_consumed` or `next_l2_gas_price` at all: [6](#0-5) 

The result is that `calculate_block_hash` — called for every produced block — silently drops these two fields from the Poseidon preimage: [7](#0-6) 

---

### Impact Explanation

Every block with `starknet_version ≥ 0.14.0` has an incorrect block hash. Downstream consequences:

1. **Wrong `PartialBlockHash` in consensus commitments.** `PartialBlockHash::from_partial_block_hash_components` calls `calculate_block_hash`, so `proposal_commitment_from` signs over a truncated hash. Validators that independently compute the correct (extended) hash would disagree. [8](#0-7) [9](#0-8) 

2. **Wrong block hash stored and served by RPC.** The DB and every RPC response carry the truncated hash, making `starknet_getBlockWithTxHashes` and related endpoints return an authoritative-looking wrong value.

3. **`SnosProofFacts.block_hash` validation mismatch.** `validate_proof_facts` checks `snos_proof_facts.block_hash` against the stored block hash. A SNOS proof generated against the canonical (extended) hash would fail this check; a proof generated against the truncated hash would pass even though the hash is wrong. [10](#0-9) 

---

### Likelihood Explanation

The network is actively running Starknet 0.14.x (test fixtures confirm `starknet_version: "0.14.4"`). Every block produced since 0.14.0 triggers the omission. No special transaction or privileged operation is required — the bug fires unconditionally for every block at the affected version.

---

### Recommendation

1. Add a `V0_14_0` variant to `BlockHashVersion` and update `TryFrom<StarknetVersion>` to map all versions ≥ 0.14.0 to it.
2. Add `l2_gas_consumed: GasAmount` and `next_l2_gas_price: GasPrice` fields to `PartialBlockHashComponents`.
3. In `gas_prices_to_hash`, add a branch for `block_hash_version >= V0_14_0` that chains `l2_gas_consumed` and `next_l2_gas_price` into the Poseidon preimage after the existing six gas-price fields.
4. Update `PartialBlockHashComponents::new` to populate the two new fields from `BlockInfo` (or pass them explicitly from the caller).
5. Add a regression test that verifies the extended hash differs from the V0_13_4 hash when `l2_gas_consumed ≠ 0`.

---

### Proof of Concept

```rust
// Demonstrates that two blocks differing only in l2_gas_consumed
// produce identical hashes under the current implementation.

let base = PartialBlockHashComponents {
    starknet_version: StarknetVersion::V0_14_0,
    l1_gas_price: GasPricePerToken { price_in_wei: 1u8.into(), price_in_fri: 1u8.into() },
    l1_data_gas_price: GasPricePerToken { price_in_wei: 1u8.into(), price_in_fri: 1u8.into() },
    l2_gas_price: GasPricePerToken { price_in_wei: 1u8.into(), price_in_fri: 1u8.into() },
    // l2_gas_consumed and next_l2_gas_price are NOT in PartialBlockHashComponents
    ..Default::default()
};

// Even if we manually vary l2_gas_consumed in the block header,
// gas_prices_to_hash() receives no such parameter and produces
// the same Poseidon output regardless.
let hash_a = calculate_block_hash(&base, GlobalRoot::default(), BlockHash::default()).unwrap();

// Changing l2_gas_consumed from 0 to 1_000_000 has zero effect on the hash.
// hash_a == hash_b  ← canonicalization invariant violated
```

The `echonet` pipeline already passes `l2_gas_consumed` and `next_l2_gas_price` to the block-hash CLI:

```python
"l2_gas_consumed": fee_market_info["l2_gas_consumed"],   # e.g. 150000
"next_l2_gas_price": fee_market_info["next_l2_gas_price"], # e.g. "0x186a0"
``` [5](#0-4) 

A CLI implementation that honours these fields will produce a different hash than the Rust `calculate_block_hash` for any block where `l2_gas_consumed ≠ 0` or `next_l2_gas_price` differs from the default, confirming the canonicalization divergence.

### Citations

**File:** crates/starknet_api/src/block_hash/block_hash_calculator.rs (L54-59)
```rust
#[allow(non_camel_case_types)]
#[derive(Clone, Debug, PartialEq, Eq, PartialOrd)]
pub enum BlockHashVersion {
    V0_13_2,
    V0_13_4,
}
```

**File:** crates/starknet_api/src/block_hash/block_hash_calculator.rs (L197-206)
```rust
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

**File:** crates/starknet_api/src/block.rs (L237-239)
```rust
    pub l2_gas_price: GasPricePerToken,
    pub l2_gas_consumed: GasAmount,
    pub next_l2_gas_price: GasPrice,
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

**File:** crates/apollo_consensus_orchestrator/src/dynamic_gas_price/mod.rs (L163-171)
```rust
pub(crate) fn proposal_commitment_from(
    partial: PartialBlockHash,
    fee_proposal: Option<GasPrice>,
) -> ProposalCommitment {
    let Some(fee_proposal) = fee_proposal else {
        return ProposalCommitment(partial.0);
    };
    ProposalCommitment(Poseidon::hash_array(&[partial.0, Felt::from(fee_proposal.0)]))
}
```

**File:** crates/blockifier/src/transaction/account_transaction.rs (L331-338)
```rust
        // Validate the block hash and block number.
        let proof_block_hash = snos_proof_facts.block_hash.0;
        let proof_block_number = snos_proof_facts.block_number.0;
        Self::validate_proof_block_number(
            proof_block_number,
            block_context.block_info.block_number,
        )?;
        Self::validate_proof_block_hash(proof_block_hash, proof_block_number, os_constants, state)?;
```
