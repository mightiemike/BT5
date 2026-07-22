### Title
`gas_prices_to_hash` Omits `l2_gas_consumed` and `next_l2_gas_price` for Starknet Versions ≥ 0.14.0, Producing Wrong Block Hash — (`crates/starknet_api/src/block_hash/block_hash_calculator.rs`)

---

### Summary

The `gas_prices_to_hash` function, which feeds the gas-price sub-hash into `calculate_block_hash`, has a hard-coded version gate that maps every Starknet version ≥ 0.13.4 — including all 0.14.x releases — to the same `BlockHashVersion::V0_13_4` branch. That branch hashes only six gas-price fields (`l1_gas_wei/fri`, `l1_data_gas_wei/fri`, `l2_gas_wei/fri`) and silently omits `l2_gas_consumed` and `next_l2_gas_price`. The echonet replay system already passes both fields to the block-hash CLI, and an in-code TODO explicitly acknowledges the omission. Because the `BlockHashVersion` enum has no variant for ≥ 0.14.0, the sequencer produces a structurally wrong block hash for every block produced under the current network version (0.14.4).

---

### Finding Description

**Version-gate collapse in `BlockHashVersion`**

`BlockHashVersion` has exactly two variants:

```rust
// crates/starknet_api/src/block_hash/block_hash_calculator.rs  lines 56-58
pub enum BlockHashVersion {
    V0_13_2,
    V0_13_4,
}
```

The `TryFrom<StarknetVersion>` conversion maps every version ≥ 0.13.4 — including 0.14.0 through 0.14.4 — to `V0_13_4`:

```rust
// lines 70-82
} else {
    Ok(Self::V0_13_4)   // catches ALL versions ≥ 0.13.4
}
```

**`gas_prices_to_hash` omits two fee-market fields**

For `V0_13_4` (which is the only branch reached for 0.14.x), the function hashes:

```rust
// lines 423-434
HashChain::new()
    .chain(&STARKNET_GAS_PRICES0)
    .chain(&l1_gas_price.price_in_wei.0.into())
    .chain(&l1_gas_price.price_in_fri.0.into())
    .chain(&l1_data_gas_price.price_in_wei.0.into())
    .chain(&l1_data_gas_price.price_in_fri.0.into())
    .chain(&l2_gas_price.price_in_wei.0.into())
    .chain(&l2_gas_price.price_in_fri.0.into())
    .get_poseidon_hash()
```

The function signature does not even accept `l2_gas_consumed` or `next_l2_gas_price`, and the in-code TODO confirms the omission is known:

```rust
// line 416
// TODO(Ayelet): add l2_gas_consumed, next_l2_gas_price after 0.14.0.
```

**`PartialBlockHashComponents` does not carry the missing fields**

The struct that feeds `calculate_block_hash` has no slots for the two fields:

```rust
// lines 212-221
pub struct PartialBlockHashComponents {
    pub header_commitments: BlockHeaderCommitments,
    pub block_number: BlockNumber,
    pub l1_gas_price: GasPricePerToken,
    pub l1_data_gas_price: GasPricePerToken,
    pub l2_gas_price: GasPricePerToken,
    pub sequencer: SequencerContractAddress,
    pub timestamp: BlockTimestamp,
    pub starknet_version: StarknetVersion,
    // l2_gas_consumed and next_l2_gas_price are absent
}
```

**Echonet already passes both fields to the CLI**

The echonet replay system, which calls the same block-hash CLI binary to verify on-chain hashes, already supplies both fields:

```python
# echonet/echo_center.py  lines 679-681
"l2_gas_price": block_info["l2_gas_price"],
"l2_gas_consumed": fee_market_info["l2_gas_consumed"],
"next_l2_gas_price": fee_market_info["next_l2_gas_price"],
```

The test fixture `central_blob.json` (version `"0.14.4"`) already contains both values:

```json
// crates/apollo_consensus_orchestrator/resources/central_blob.json  lines 92-95
"fee_market_info": {
    "l2_gas_consumed": 150000,
    "next_l2_gas_price": "0x186a0"
}
```

**`StorageBlockHeader` and `BlockHeaderWithoutHash` store both fields**

Both the storage schema and the wire-format protobuf converter carry `l2_gas_consumed` and `next_l2_gas_price` faithfully, confirming they are first-class header fields that must enter the hash:

```rust
// crates/apollo_storage/src/header.rs  lines 87-89
pub l2_gas_consumed: GasAmount,
pub next_l2_gas_price: GasPrice,
```

```rust
// crates/apollo_protobuf/src/converters/header.rs  lines 174-178
let l2_gas_consumed = value.l2_gas_consumed.into();
let next_l2_gas_price = u128::from(
    value.next_l2_gas_price.ok_or(missing("SignedBlockHeader::next_l2_gas_price"))?,
).into();
```

---

### Impact Explanation

**Critical — Wrong block hash committed for every block with Starknet version ≥ 0.14.0.**

`calculate_block_hash` is the canonical function used by the sequencer to produce the block hash that is:
- stored in `apollo_storage` as `StorageBlockHeader::block_hash`
- chained as `previous_block_hash` into every subsequent block
- signed by validators during consensus
- returned by RPC as the authoritative block identifier

Because `l2_gas_consumed` and `next_l2_gas_price` are omitted from the gas-prices sub-hash, the sequencer produces a hash that differs from the one the echonet/CLI computes for the same block. Any verifier or replay system that includes these fields will disagree with the sequencer on every block hash from version 0.14.0 onward. This is a wrong-state-value impact: the committed block hash is structurally incorrect, and the error propagates to every subsequent block through `previous_block_hash`.

---

### Likelihood Explanation

**High.** The network is already running at version 0.14.4. Every block produced since 0.14.0 is affected. The echonet already passes the missing fields to the CLI, meaning the divergence is observable in any replay or cross-validation run. The TODO comment confirms the omission is known but unimplemented.

---

### Recommendation

1. Add a `V0_14_0` variant to `BlockHashVersion` and extend `TryFrom<StarknetVersion>` to map versions ≥ 0.14.0 to it.
2. Add `l2_gas_consumed: GasAmount` and `next_l2_gas_price: GasPrice` to `PartialBlockHashComponents`.
3. Update `gas_prices_to_hash` to accept and chain both fields when `block_hash_version >= V0_14_0`, producing:
   ```
   Poseidon("STARKNET_GAS_PRICES0", l1_gas_wei, l1_gas_fri,
            l1_data_gas_wei, l1_data_gas_fri,
            l2_gas_wei, l2_gas_fri,
            l2_gas_consumed, next_l2_gas_price)
   ```
4. Update `PartialBlockHashComponents::new` to populate the two new fields from `BlockInfo`.
5. Add a regression test that asserts the hash changes when either field is modified, analogous to the existing `test_hash_changes!` macro.

---

### Proof of Concept

The divergence is directly observable by comparing the two code paths for a block at version 0.14.4:

**Sequencer path** (`calculate_block_hash` → `gas_prices_to_hash`):

```rust
// BlockHashVersion::try_from(StarknetVersion::V0_14_4) → Ok(V0_13_4)
// gas_prices_to_hash with V0_13_4:
Poseidon("STARKNET_GAS_PRICES0",
    l1_gas_wei, l1_gas_fri,
    l1_data_gas_wei, l1_data_gas_fri,
    l2_gas_wei, l2_gas_fri)
// l2_gas_consumed=150000 and next_l2_gas_price=0x186a0 are NOT included
```

**Echonet/CLI path** (`_compute_block_hash` in `echo_center.py`):

```python
"l2_gas_consumed": fee_market_info["l2_gas_consumed"],   # 150000
"next_l2_gas_price": fee_market_info["next_l2_gas_price"],  # 0x186a0
# Both fields are passed to the CLI and included in the hash
```

For the test fixture block (`starknet_version: "0.14.4"`, `l2_gas_consumed: 150000`, `next_l2_gas_price: 0x186a0`), the sequencer's `gas_prices_to_hash` produces a Poseidon hash over 7 elements while the CLI produces a hash over 9 elements. The resulting block hashes are different values, and the sequencer's value is wrong. [1](#0-0) [2](#0-1) [3](#0-2) [4](#0-3) [5](#0-4) [6](#0-5) [7](#0-6) [8](#0-7)

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

**File:** crates/apollo_storage/src/header.rs (L72-114)
```rust
#[derive(Debug, Default, Clone, Eq, PartialEq, Hash, Deserialize, Serialize, PartialOrd, Ord)]
pub struct StorageBlockHeader {
    /// The hash of this block.
    pub block_hash: BlockHash,
    /// The hash of this block's parent.
    pub parent_hash: BlockHash,
    /// The number of this block.
    pub block_number: BlockNumber,
    /// The L1 gas price per token.
    pub l1_gas_price: GasPricePerToken,
    /// The L1 data gas price per token.
    pub l1_data_gas_price: GasPricePerToken,
    /// The L2 gas price per token.
    pub l2_gas_price: GasPricePerToken,
    /// The amount of L2 gas consumed.
    pub l2_gas_consumed: GasAmount,
    /// The next L2 gas price.
    pub next_l2_gas_price: GasPrice,
    /// The state root after this block.
    pub state_root: GlobalRoot,
    /// The sequencer address that created this block.
    pub sequencer: SequencerContractAddress,
    /// The timestamp of this block.
    pub timestamp: BlockTimestamp,
    /// The L1 data availability mode.
    pub l1_da_mode: L1DataAvailabilityMode,
    /// The state diff commitment, if available.
    pub state_diff_commitment: Option<StateDiffCommitment>,
    /// The transaction commitment, if available.
    pub transaction_commitment: Option<TransactionCommitment>,
    /// The event commitment, if available.
    pub event_commitment: Option<EventCommitment>,
    /// The receipt commitment, if available.
    pub receipt_commitment: Option<ReceiptCommitment>,
    /// The length of the state diff, if available.
    pub state_diff_length: Option<usize>,
    /// The number of transactions in this block.
    pub n_transactions: usize,
    /// The number of events in this block.
    pub n_events: usize,
    /// Proposer's oracle-derived recommended L2 gas fee. `None` for pre-V0_14_3 blocks.
    pub fee_proposal_fri: Option<GasPrice>,
}
```

**File:** crates/apollo_protobuf/src/converters/header.rs (L174-211)
```rust
        let l2_gas_consumed = value.l2_gas_consumed.into();
        let next_l2_gas_price = u128::from(
            value.next_l2_gas_price.ok_or(missing("SignedBlockHeader::next_l2_gas_price"))?,
        )
        .into();
        let fee_proposal_fri = value.fee_proposal_fri.map(|v| GasPrice(u128::from(v)));

        let receipt_commitment = value
            .receipts
            .map(|receipts| receipts.try_into().map(ReceiptCommitment))
            .transpose()?;

        let state_diff_commitment = value
            .state_diff_commitment
            .ok_or(missing("SignedBlockHeader::state_diff_commitment"))?
            .root
            .map(|root| root.try_into())
            .transpose()?
            .map(|hash| StateDiffCommitment(PoseidonHash(hash)));

        Ok(SignedBlockHeader {
            block_header: BlockHeader {
                block_hash,
                block_header_without_hash: BlockHeaderWithoutHash {
                    parent_hash,
                    block_number: BlockNumber(value.number),
                    l1_gas_price,
                    l1_data_gas_price,
                    l2_gas_price,
                    l2_gas_consumed,
                    next_l2_gas_price,
                    state_root,
                    sequencer,
                    timestamp,
                    l1_da_mode,
                    starknet_version,
                    fee_proposal_fri,
                },
```

**File:** crates/apollo_consensus_orchestrator/resources/central_blob.json (L92-95)
```json
  "fee_market_info": {
    "l2_gas_consumed": 150000,
    "next_l2_gas_price": "0x186a0"
  },
```
