### Title
`gas_prices_to_hash` silently omits `l2_gas_consumed` and `next_l2_gas_price` for Starknet versions ≥ 0.14.0, producing wrong block hashes — (`crates/starknet_api/src/block_hash/block_hash_calculator.rs`)

---

### Summary

The `gas_prices_to_hash` function carries an explicit TODO acknowledging that `l2_gas_consumed` and `next_l2_gas_price` must be added to the gas-prices preimage "after 0.14.0". The current production version is 0.14.4 and those fields were never added. `PartialBlockHashComponents` has no slots for them, and `BlockHashInput::to_final_block_hash_components` in the CLI silently drops them even though the echonet verification tool explicitly supplies them. Every block produced since 0.14.0 therefore carries a block hash computed from an incomplete preimage.

---

### Finding Description

**Root cause — `gas_prices_to_hash` missing fields**

`gas_prices_to_hash` in `crates/starknet_api/src/block_hash/block_hash_calculator.rs` (lines 417–443) builds the gas-prices sub-hash that is chained into the full block hash. For `BlockHashVersion >= V0_13_4` it hashes exactly seven felts:

```
Poseidon("STARKNET_GAS_PRICES0",
         l1_gas_price_wei, l1_gas_price_fri,
         l1_data_gas_price_wei, l1_data_gas_price_fri,
         l2_gas_price_wei, l2_gas_price_fri)
```

The function carries the comment:

```rust
// TODO(Ayelet): add l2_gas_consumed, next_l2_gas_price after 0.14.0.
```

The current `LATEST` version is `StarknetVersion::V0_14_4`. The two fields were never added.

**Structural gap — `PartialBlockHashComponents` has no slots**

`PartialBlockHashComponents` (lines 212–221 of the same file) stores only `l1_gas_price`, `l1_data_gas_price`, and `l2_gas_price`. It has no `l2_gas_consumed` or `next_l2_gas_price` fields, so there is no path by which those values could reach `gas_prices_to_hash` even if the caller possessed them.

**Silent drop in the CLI conversion**

`BlockHashInput::to_final_block_hash_components` in `crates/starknet_committer_and_os_cli/src/committer_cli/block_hash.rs` (lines 27–44) accepts a `BlockHeaderWithoutHash`, which *does* carry both `l2_gas_consumed` and `next_l2_gas_price` (defined in `crates/starknet_api/src/block.rs` lines 238–239). The conversion maps only the three gas-price fields and silently discards the other two:

```rust
PartialBlockHashComponents {
    starknet_version: self.header.starknet_version,
    header_commitments: self.block_commitments,
    block_number: self.header.block_number,
    l1_gas_price: self.header.l1_gas_price,
    l1_data_gas_price: self.header.l1_data_gas_price,
    l2_gas_price: self.header.l2_gas_price,   // l2_gas_consumed and
    sequencer: self.header.sequencer,          // next_l2_gas_price are
    timestamp: self.header.timestamp,          // silently dropped here
}
```

**Echonet supplies the fields; CLI drops them**

`echonet/echo_center.py` `_compute_block_hash` (lines 680–681) explicitly passes both fields to the CLI:

```python
"l2_gas_consumed": fee_market_info["l2_gas_consumed"],
"next_l2_gas_price": fee_market_info["next_l2_gas_price"],
```

Because the CLI drops them, the echonet's verification hash is computed from the same incomplete preimage as the sequencer's, so the echonet cannot detect the divergence from the canonical specification.

---

### Impact Explanation

The block hash is the canonical identifier committed to L1 and returned by every RPC `starknet_getBlockWithTxHashes` / `starknet_getBlockWithTxs` call. If the canonical Starknet specification for versions ≥ 0.14.0 includes `l2_gas_consumed` and `next_l2_gas_price` in the gas-prices sub-hash (as the TODO and the echonet code both indicate), then:

- Every block hash produced by the sequencer since 0.14.0 is wrong.
- The echonet verification tool silently accepts the wrong hash because it uses the same incomplete formula.
- Any external verifier or L1 contract using the correct formula will disagree with the sequencer's committed hashes.

This matches the Critical scope: *wrong storage value / block hash from execution logic for accepted input*, and the High scope: *RPC returns an authoritative-looking wrong value*.

---

### Likelihood Explanation

The TODO is explicit and version-gated ("after 0.14.0"). The current production version is 0.14.4 — four minor versions past the stated threshold. The echonet actively passes the missing fields to the CLI, confirming the intent to include them. The trigger is automatic: every block produced at version ≥ 0.14.0 exercises the incomplete path.

---

### Recommendation

1. Add `l2_gas_consumed: GasAmount` and `next_l2_gas_price: GasPrice` to `PartialBlockHashComponents`.
2. Extend `gas_prices_to_hash` to chain these two values for `BlockHashVersion >= V0_13_4` (or introduce a new `BlockHashVersion::V0_14_0` gate if the spec change is version-specific).
3. Update `BlockHashInput::to_final_block_hash_components` to map `self.header.l2_gas_consumed` and `self.header.next_l2_gas_price` into the new fields.
4. Add a regression test that verifies the gas-prices sub-hash changes when `l2_gas_consumed` or `next_l2_gas_price` changes for a 0.14.x block.

---

### Proof of Concept

**Step 1 — TODO in `gas_prices_to_hash`** [1](#0-0) 

The comment on line 416 reads: `// TODO(Ayelet): add l2_gas_consumed, next_l2_gas_price after 0.14.0.` The function body for `>= V0_13_4` chains only six price felts and returns; the two fee-market fields are never referenced.

**Step 2 — `PartialBlockHashComponents` has no slots for the missing fields** [2](#0-1) 

`l2_gas_consumed` and `next_l2_gas_price` are absent from the struct, so no caller can supply them.

**Step 3 — `BlockHeaderWithoutHash` carries both fields** [3](#0-2) 

Lines 238–239 show `l2_gas_consumed` and `next_l2_gas_price` are present in the full header.

**Step 4 — CLI conversion silently drops them** [4](#0-3) 

`to_final_block_hash_components` maps only `l1_gas_price`, `l1_data_gas_price`, and `l2_gas_price`; `l2_gas_consumed` and `next_l2_gas_price` from `self.header` are never read.

**Step 5 — Echonet supplies the fields; they are silently discarded** [5](#0-4) 

Lines 680–681 pass `l2_gas_consumed` and `next_l2_gas_price` to the CLI. Because the CLI drops them, the echonet computes the same incomplete hash as the sequencer and cannot detect the divergence.

### Citations

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

**File:** crates/starknet_api/src/block_hash/block_hash_calculator.rs (L416-434)
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
