### Title
`gas_prices_to_hash` omits `l2_gas_consumed` and `next_l2_gas_price` for Starknet versions ≥ 0.14.0, producing wrong block hashes — (File: crates/starknet_api/src/block_hash/block_hash_calculator.rs)

---

### Summary

The `gas_prices_to_hash` function computes the gas-prices sub-hash that is chained into every block hash. For all versions ≥ 0.13.4 it hashes exactly six fields (three gas prices × wei/fri). A developer TODO inside the function acknowledges that `l2_gas_consumed` and `next_l2_gas_price` must be added for versions ≥ 0.14.0, but the `BlockHashVersion` enum has no variant beyond `V0_13_4`, so every 0.14.x block silently uses the old six-field formula. The echonet OS-verification service already forwards both missing fields to the block-hash CLI, confirming the canonical spec includes them. The result is that the sequencer produces a structurally wrong block hash for every block on the live 0.14.x network.

---

### Finding Description

**Root cause — `gas_prices_to_hash` missing two fields:**

```
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
                .get_poseidon_hash(),   // ← l2_gas_consumed and next_l2_gas_price absent
        ]
    } ...
}
``` [1](#0-0) 

**Version gate is frozen at `V0_13_4` — no `V0_14_0` variant exists:**

```rust
pub enum BlockHashVersion {
    V0_13_2,
    V0_13_4,   // all versions ≥ 0.13.4 collapse here, including 0.14.4
}
``` [2](#0-1) 

The `TryFrom<StarknetVersion>` implementation maps every version ≥ 0.13.4 to `V0_13_4`, so the six-field formula is used unconditionally for all 0.14.x blocks. [3](#0-2) 

**`PartialBlockHashComponents` does not carry the missing fields:**

```rust
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
``` [4](#0-3) 

Yet `BlockHeaderWithoutHash` — the authoritative block-header type — stores both fields:

```rust
pub struct BlockHeaderWithoutHash {
    ...
    pub l2_gas_consumed: GasAmount,
    pub next_l2_gas_price: GasPrice,
    ...
}
``` [5](#0-4) 

**Echonet OS-verification service already passes both fields to the canonical CLI:**

```python
result = self._run_block_hash_cli(
    "block-hash",
    {
        "header": {
            ...
            "l2_gas_price": block_info["l2_gas_price"],
            "l2_gas_consumed": fee_market_info["l2_gas_consumed"],   # ← present
            "next_l2_gas_price": fee_market_info["next_l2_gas_price"], # ← present
            ...
        },
        ...
    },
)
``` [6](#0-5) 

The echonet is the OS-verification service that cross-checks sequencer output against the canonical Starknet OS. Its block-hash CLI call already includes `l2_gas_consumed` and `next_l2_gas_price`, confirming the canonical spec requires them for 0.14.x. The Rust `gas_prices_to_hash` does not include them, so the Rust code and the canonical CLI produce different Poseidon hashes for the same block.

**Divergent values (concrete):**

| Component | Rust `gas_prices_to_hash` (V0_13_4 path) | Canonical spec (≥ 0.14.0) |
|---|---|---|
| `STARKNET_GAS_PRICES0` | ✓ | ✓ |
| `l1_gas_price_wei` | ✓ | ✓ |
| `l1_gas_price_fri` | ✓ | ✓ |
| `l1_data_gas_price_wei` | ✓ | ✓ |
| `l1_data_gas_price_fri` | ✓ | ✓ |
| `l2_gas_price_wei` | ✓ | ✓ |
| `l2_gas_price_fri` | ✓ | ✓ |
| `l2_gas_consumed` | **✗ missing** | ✓ |
| `next_l2_gas_price` | **✗ missing** | ✓ |

For any 0.14.x block where `l2_gas_consumed > 0` (i.e., every normal block), the Poseidon hash of the gas-prices sub-tree differs, causing `calculate_block_hash` to return a wrong `BlockHash`. [7](#0-6) 

---

### Impact Explanation

`calculate_block_hash` is called during block production, consensus finalization (`PartialBlockHash`), and state-sync verification. A wrong block hash means:

- The sequencer commits blocks whose `block_hash` field does not match the canonical Starknet hash for that block.
- Any verifier (echonet, L1 contract, external explorer) that uses the correct formula will reject every 0.14.x block.
- `PartialBlockHash` used in consensus pre-commits will diverge between nodes that upgrade the CLI and nodes running the Rust code, breaking BFT agreement.

This matches **Critical — Wrong state, receipt, event, L1 message, class hash, storage value, or revert result from blockifier/syscall/execution logic for accepted input**.

---

### Likelihood Explanation

The current live Starknet version is 0.14.4 (confirmed by test fixtures `block_post_0_14_3.json`, `block_post_0_14_4.json`). Every block produced since 0.14.0 activation triggers the wrong code path. The trigger requires no special privileges — any submitted transaction that causes `l2_gas_consumed > 0` (i.e., any normal transaction) is sufficient.

---

### Recommendation

1. Add `BlockHashVersion::V0_14_0` to the enum and update `TryFrom<StarknetVersion>` to map versions ≥ 0.14.0 to it.
2. Add `l2_gas_consumed: GasAmount` and `next_l2_gas_price: GasPrice` to `PartialBlockHashComponents`.
3. Update `gas_prices_to_hash` to chain these two fields for `block_hash_version >= V0_14_0`:
   ```rust
   if block_hash_version >= &BlockHashVersion::V0_14_0 {
       HashChain::new()
           .chain(&STARKNET_GAS_PRICES0)
           ... // existing six fields
           .chain(&l2_gas_consumed.0.into())
           .chain(&next_l2_gas_price.0.into())
           .get_poseidon_hash()
   }
   ```
4. Update `PartialBlockHashComponents::new` to populate the two new fields from `BlockInfo` / `FeeMarketInfo`.

---

### Proof of Concept

Take any 0.14.4 block from the test corpus (e.g., `block_post_0_14_3.json` which has `l2_gas_consumed: 988191555`, `next_l2_gas_price: 0x1dcd65000`):

```
Rust hash input (6 fields):
  Poseidon("STARKNET_GAS_PRICES0",
           0x3b9aca00, 0xe8d4a51000,   // l1_gas wei/fri
           0x1,        0x3e8,           // l1_data_gas wei/fri
           0x7a1200,   0x1dcd65000)     // l2_gas wei/fri
  → H_rust

Canonical hash input (8 fields):
  Poseidon("STARKNET_GAS_PRICES0",
           0x3b9aca00, 0xe8d4a51000,
           0x1,        0x3e8,
           0x7a1200,   0x1dcd65000,
           988191555,                   // l2_gas_consumed  ← missing in Rust
           0x1dcd65000)                 // next_l2_gas_price ← missing in Rust
  → H_canonical ≠ H_rust
```

`H_rust ≠ H_canonical` for every block where `l2_gas_consumed ≠ 0`, which is every non-empty block on the 0.14.x network. [8](#0-7)

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

**File:** crates/starknet_api/src/block_hash/block_hash_calculator.rs (L416-433)
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

**File:** crates/apollo_starknet_client/resources/reader/block_post_0_14_3.json (L1188-1192)
```json
    "starknet_version": "0.14.4",
    "l2_gas_consumed": 988191555,
    "next_l2_gas_price": "0x1dcd65000",
    "fee_proposal_fri": "0x1dcd66000"
}
```
