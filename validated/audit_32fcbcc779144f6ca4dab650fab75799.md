### Title
`gas_prices_to_hash` omits `l2_gas_consumed` and `next_l2_gas_price` from block hash for all V0_14.x blocks, enabling P2P fee-market state manipulation - (File: `crates/starknet_api/src/block_hash/block_hash_calculator.rs`)

---

### Summary

The `gas_prices_to_hash` function used inside `calculate_block_hash` does not commit `l2_gas_consumed` or `next_l2_gas_price` to the block hash for any Starknet version, including all current V0_14.x blocks. A TODO comment in the source explicitly marks these fields as pending inclusion "after 0.14.0", but no `BlockHashVersion` variant or version gate was ever added. Because the block hash does not bind these fields, a malicious P2P peer can forward a `SignedBlockHeader` with valid hash and consensus signatures but an arbitrarily modified `next_l2_gas_price`. A syncing node accepts the header (hash check passes), stores the tampered value, and uses it as the gas price for the next block — directly corrupting fee accounting with economic impact.

---

### Finding Description

**Version/hash boundary — missing fields in `gas_prices_to_hash`**

`gas_prices_to_hash` in `block_hash_calculator.rs` is the sole function that encodes gas-price data into the block hash. For `BlockHashVersion::V0_13_4` (which covers every version ≥ 0.13.4, including all 0.14.x releases), it hashes only six values: `l1_gas_price_{wei,fri}`, `l1_data_gas_price_{wei,fri}`, and `l2_gas_price_{wei,fri}`. [1](#0-0) 

The comment immediately above the function body reads:

```
// TODO(Ayelet): add l2_gas_consumed, next_l2_gas_price after 0.14.0.
``` [2](#0-1) 

The `BlockHashVersion` enum has only two variants (`V0_13_2`, `V0_13_4`); every version ≥ 0.13.4 collapses to `V0_13_4` with no further discrimination. [3](#0-2) 

`BlockHeaderWithoutHash` — the canonical header struct — carries both `l2_gas_consumed` and `next_l2_gas_price` as first-class fields. [4](#0-3) 

`PartialBlockHashComponents::new()` converts a `BlockInfo` into the hash-input struct but silently drops both fields. [5](#0-4) 

The CLI entry point `BlockHashInput::to_final_block_hash_components()` also drops them when building `PartialBlockHashComponents`. [6](#0-5) 

The P2P protobuf `SignedBlockHeader` carries both fields over the wire. [7](#0-6) 

The protobuf converter faithfully deserialises them into `BlockHeaderWithoutHash`. [8](#0-7) 

The echonet replay tool `_compute_block_hash` already passes both fields to the CLI, anticipating their inclusion in the hash — but the Rust CLI silently ignores them. [9](#0-8) 

**Sync path uses `next_l2_gas_price` directly from the received header.** When a node syncs a block, it reads `next_l2_gas_price` from the incoming `SyncBlock` and stores it as the gas price for the next block, as confirmed by the test: [10](#0-9) 

Because the block hash does not bind `next_l2_gas_price`, a peer can alter it in a `SignedBlockHeader` without touching the hash or the consensus signatures. The receiving node's hash verification passes, and the tampered value is stored and used.

---

### Impact Explanation

A malicious P2P peer intercepts or crafts a `SignedBlockHeader` for any V0_14.x block, replaces `next_l2_gas_price` with an arbitrary value (e.g., 0 or `u128::MAX`), and forwards it to a syncing node. The node verifies the block hash (which does not cover `next_l2_gas_price`), accepts the header, and uses the attacker-controlled value as the L2 gas price for the next block. Every transaction in that block is then priced against the wrong gas price, producing incorrect fees, incorrect bouncer accounting, and incorrect balance changes — matching the "Incorrect fee, gas, bouncer, resource accounting, refund, balance, or L1 gas price effect with economic impact" critical impact category.

---

### Likelihood Explanation

The attack requires only a network-level position as a P2P peer. No privileged sequencer key is needed. The attacker modifies a single integer field in a protobuf message; the block hash and signatures remain fully valid. Any syncing node (including full nodes and validators catching up) is affected.

---

### Recommendation

1. Add a new `BlockHashVersion::V0_14_0` (or `V0_14_1`) variant to `BlockHashVersion` and extend the `TryFrom<StarknetVersion>` conversion to map versions ≥ 0.14.x to it.
2. Add `l2_gas_consumed` and `next_l2_gas_price` to `PartialBlockHashComponents` and populate them in `PartialBlockHashComponents::new()`.
3. Extend `gas_prices_to_hash` with a new branch for the new version that chains `l2_gas_consumed` and `next_l2_gas_price` into the `STARKNET_GAS_PRICES0` Poseidon hash.
4. Update `BlockHashInput::to_final_block_hash_components()` in the CLI to forward these fields.

---

### Proof of Concept

```
1. Obtain a valid SignedBlockHeader for a V0_14.x block with valid consensus signatures.
   (block_hash commits to: block_number, state_root, sequencer, timestamp,
    concat_counts, commitments, gas_prices[l1/l1_data/l2], starknet_version,
    0, parent_hash — NOT next_l2_gas_price)

2. Modify next_l2_gas_price in the protobuf message to 0 (or u128::MAX).
   The block_hash field and all ConsensusSignature entries are unchanged.

3. Forward the modified SignedBlockHeader to a syncing node via the P2P layer.

4. The node calls calculate_block_hash() → gas_prices_to_hash() which does not
   read next_l2_gas_price → hash matches → block accepted.

5. The node stores next_l2_gas_price = 0 (or MAX) in BlockHeaderWithoutHash.

6. On the next height, context.l2_gas_price is set from the stored
   next_l2_gas_price (confirmed by test_first_height_keeps_sync_provided_l2_gas_price).
   All fee calculations for the next block use the attacker-controlled price.
```

### Citations

**File:** crates/starknet_api/src/block_hash/block_hash_calculator.rs (L70-82)
```rust
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

**File:** crates/apollo_protobuf/src/protobuf/protoc_output.rs (L1214-1217)
```rust
    #[prost(uint64, tag = "18")]
    pub l2_gas_consumed: u64,
    #[prost(message, optional, tag = "19")]
    pub next_l2_gas_price: ::core::option::Option<Uint128>,
```

**File:** crates/apollo_protobuf/src/converters/header.rs (L174-178)
```rust
        let l2_gas_consumed = value.l2_gas_consumed.into();
        let next_l2_gas_price = u128::from(
            value.next_l2_gas_price.ok_or(missing("SignedBlockHeader::next_l2_gas_price"))?,
        )
        .into();
```

**File:** echonet/echo_center.py (L679-681)
```python
                    "l2_gas_price": block_info["l2_gas_price"],
                    "l2_gas_consumed": fee_market_info["l2_gas_consumed"],
                    "next_l2_gas_price": fee_market_info["next_l2_gas_price"],
```

**File:** crates/apollo_consensus_orchestrator/src/sequencer_consensus_context_test.rs (L1727-1740)
```rust
        sync_block.block_header_without_hash.next_l2_gas_price = GasPrice(SYNCED_NEXT_L2_GAS_PRICE);
        Ok(sync_block)
    });
    deps.setup_default_expectations();
    deps.batcher.expect_add_sync_block().times(1).return_once(|_| Ok(()));
    deps.batcher.expect_start_height().times(2).returning(|_| Ok(()));

    let mut context = deps.build_context();
    context.config.dynamic_config.min_l2_gas_price_per_height =
        vec![PricePerHeight { height: 250, price: CONFIG_MIN_PRICE_AT_250 }];

    // Sync succeeds at height 200, l2_gas_price is taken from synced next_l2_gas_price.
    assert!(context.try_sync(SYNC_HEIGHT).await);
    assert_eq!(context.l2_gas_price, GasPrice(SYNCED_NEXT_L2_GAS_PRICE));
```
