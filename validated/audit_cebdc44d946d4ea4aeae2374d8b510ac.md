### Title
`fee_proposal_fri` Excluded from Block Hash Allows P2P Peer to Inject Arbitrary Values into `fee_actual` Window, Shifting L2 Gas Price Floor for All Users — (`crates/starknet_api/src/block.rs`, `crates/starknet_api/src/block_hash/block_hash_calculator.rs`, `crates/apollo_protobuf/src/proto/p2p/proto/sync/header.proto`)

---

### Summary

`fee_proposal_fri` is stored in `BlockHeaderWithoutHash`, transmitted in `SignedBlockHeader` over P2P sync, and used as the sole input to `compute_fee_actual` — the sliding-window median that sets the minimum floor for the next block's L2 gas price. However, `fee_proposal_fri` is **explicitly excluded from the block hash**. A malicious P2P peer can therefore send a `SignedBlockHeader` with any `fee_proposal_fri` value, the receiving node cannot detect the tampering (the block hash and signatures are still valid), and the injected values corrupt the `fee_actual` window, shifting the L2 gas price floor for every subsequent block and every user.

---

### Finding Description

**Step 1 — `fee_proposal_fri` is not in the block hash.**

`BlockHeaderWithoutHash` carries the field with an explicit TODO:

```rust
// TODO(AndrewL): Add this field into the block hash.
/// Proposer's oracle-derived recommended L2 gas fee. `None` for pre-V0_14_3 blocks.
pub fee_proposal_fri: Option<GasPrice>,
``` [1](#0-0) 

`PartialBlockHashComponents` — the only struct fed into `calculate_block_hash` — does not contain `fee_proposal_fri`:

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
}
``` [2](#0-1) 

The P2P protobuf schema itself carries the warning in both the `.proto` source and the generated Rust:

```proto
// WARNING: this field is currently not part of the block hash, so the value must be trusted.
optional Uint128 fee_proposal_fri = 22;
``` [3](#0-2) [4](#0-3) 

**Step 2 — `fee_proposal_fri` is the sole input to `fee_actual`, which floors the next L2 gas price.**

`compute_fee_actual` computes the median of `fee_proposal_fri` values over a sliding window of `fee_proposal_window_size` (= 10) blocks: [5](#0-4) 

`calculate_next_l2_gas_price_for_fin` uses `fee_actual` as a hard floor:

```rust
let effective_min = match fee_actual {
    Some(fa) => GasPrice(max(config_min.0, fa.0)),
    None => config_min,
};
calculate_next_base_gas_price(current_l2_gas_price, l2_gas_used, gas_target, effective_min)
``` [6](#0-5) 

**Step 3 — P2P sync stores `fee_proposal_fri` from the peer without hash-based validation.**

The protobuf converter for `SignedBlockHeader` faithfully copies `fee_proposal_fri` into `BlockHeaderWithoutHash`: [7](#0-6) 

`initialize_fee_proposals_window` reads `fee_proposal_fri` directly from storage (populated by P2P sync) and records it into the in-memory window:

```rust
match self.deps.state_sync_client.get_block(block_number).await {
    Ok(block) => self.record_fee_proposal(
        block_number,
        block.block_header_without_hash.fee_proposal_fri,
    ),
``` [8](#0-7) 

`try_sync` does the same at runtime:

```rust
self.record_fee_proposal(height, sync_block.block_header_without_hash.fee_proposal_fri);
``` [9](#0-8) 

**Step 4 — No bounds check is applied to `fee_proposal_fri` during P2P sync.**

The margin-bounds check (`fee_proposal_bounds`) is only enforced during live consensus proposal validation (`is_proposal_init_valid`): [10](#0-9) 

It is never applied when a node reads `fee_proposal_fri` from a synced block header. A peer can therefore supply any value — including `u128::MAX` — for every block in the 10-block window.

---

### Impact Explanation

A malicious P2P peer that serves `SignedBlockHeader` messages (e.g., during initial sync or after a restart that triggers `initialize_fee_proposals_window`) can inject arbitrary `fee_proposal_fri` values for up to 10 consecutive blocks. Because the block hash does not commit to this field, the injected headers pass all signature and hash verification. The corrupted window then causes `compute_fee_actual` to return an attacker-controlled median, which `calculate_next_l2_gas_price_for_fin` uses as the hard minimum for the next block's L2 gas price. Setting the window to `u128::MAX` makes the minimum gas price `u128::MAX`, causing every subsequent transaction to be rejected by the mempool (gas price below minimum) or to require an astronomically high fee. Setting it to zero removes the oracle-derived floor, potentially enabling fee manipulation in the opposite direction.

This matches: **Critical — Incorrect fee, gas, bouncer, resource accounting, refund, balance, or L1 gas price effect with economic impact** and **High — Mempool/gateway/RPC admission rejects valid transactions before sequencing**.

---

### Likelihood Explanation

Any node that syncs from a peer (including during `initialize_fee_proposals_window` at startup) is exposed. The attacker only needs to serve valid `SignedBlockHeader` messages — the block hash and signatures are correct; only the unprotected `fee_proposal_fri` field is manipulated. No privileged access is required. The attack is reachable from any peer in the P2P network.

---

### Recommendation

Include `fee_proposal_fri` in `PartialBlockHashComponents` and in `calculate_block_hash` (resolving the existing `TODO(AndrewL)`). Until then, apply the same margin-bounds check used in `is_proposal_init_valid` when recording `fee_proposal_fri` from synced blocks, and reject or clamp values that fall outside the allowed range relative to the previously recorded window.

---

### Proof of Concept

1. Attacker runs a P2P node and serves `SignedBlockHeader` responses for blocks `[H-10, H-1]`.
2. For each header, the attacker sets `fee_proposal_fri = u128::MAX` while keeping all other fields (including `block_hash` and `signatures`) identical to the canonical block.
3. A victim node starting up calls `initialize_fee_proposals_window(H)`, which calls `state_sync_client.get_block(n)` for each `n` in `[H-10, H-1]`. If the victim's storage was populated from the attacker's headers, `fee_proposal_fri = u128::MAX` is recorded for all 10 slots.
4. `compute_fee_actual` returns `u128::MAX` as the median.
5. `calculate_next_l2_gas_price_for_fin` sets `effective_min = u128::MAX`.
6. `calculate_next_base_gas_price` returns `u128::MAX` as the next L2 gas price.
7. Every transaction submitted to the victim node is rejected by the mempool because its `max_price_per_unit` for L2 gas is below `u128::MAX`, or the fee transfer overflows.

The block hash check at step 3 passes because `fee_proposal_fri` is not part of the hash preimage. [11](#0-10) [12](#0-11) [5](#0-4) [13](#0-12)

### Citations

**File:** crates/starknet_api/src/block.rs (L245-247)
```rust
    // TODO(AndrewL): Add this field into the block hash.
    /// Proposer's oracle-derived recommended L2 gas fee. `None` for pre-V0_14_3 blocks.
    pub fee_proposal_fri: Option<GasPrice>,
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

**File:** crates/apollo_protobuf/src/proto/p2p/proto/sync/header.proto (L34-36)
```text
    // Proposer's oracle-derived recommended fee. Absent for pre-V0_14_3 blocks.
    // WARNING: this field is currently not part of the block hash, so the value must be trusted.
    optional Uint128 fee_proposal_fri = 22;
```

**File:** crates/apollo_protobuf/src/protobuf/protoc_output.rs (L1223-1228)
```rust
    /// Proposer's oracle-derived recommended fee. Absent for pre-V0_14_3 blocks.
    /// WARNING: this field is currently not part of the block hash, so the value must be trusted.
    ///
    /// can be more explicit here about the signature structure as this is not part of account abstraction
    #[prost(message, optional, tag = "22")]
    pub fee_proposal_fri: ::core::option::Option<Uint128>,
```

**File:** crates/apollo_consensus_orchestrator/src/dynamic_gas_price/mod.rs (L56-92)
```rust
pub fn compute_fee_actual(
    fee_proposals_window: &BTreeMap<BlockNumber, Option<GasPrice>>,
    height: BlockNumber,
    window_size: u64,
) -> Option<GasPrice> {
    let Some(start) = height.0.checked_sub(window_size) else {
        warn!(
            "Cannot compute fee_actual for height {height}: height is below window_size \
             ({window_size})"
        );
        return None;
    };
    let window_size_usize = usize::try_from(window_size).expect("window_size fits in usize");
    let mut window = Vec::with_capacity(window_size_usize);
    for source_height in (start..height.0).map(BlockNumber) {
        match fee_proposals_window.get(&source_height) {
            Some(Some(price)) => window.push(*price),
            Some(None) | None => {
                warn!(
                    "Cannot compute fee_actual for height {height}: fee_proposals_window has no \
                     recorded fee_proposal for height {source_height}"
                );
                return None;
            }
        }
    }
    window.sort();
    let mid = window_size_usize / 2;
    let median = if window_size_usize.is_multiple_of(2) {
        // Even: average of the two middle values, rounded down.
        // Overflow-safe averaging: a + (b - a) / 2 (safe because sorted, so b >= a).
        GasPrice(window[mid - 1].0 + (window[mid].0 - window[mid - 1].0) / 2)
    } else {
        window[mid]
    };
    Some(median)
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

**File:** crates/apollo_protobuf/src/converters/header.rs (L179-211)
```rust
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

**File:** crates/apollo_consensus_orchestrator/src/sequencer_consensus_context.rs (L326-341)
```rust
    pub async fn initialize_fee_proposals_window(
        &mut self,
        start_height: BlockNumber,
    ) -> StateSyncClientResult<()> {
        const STATE_SYNC_RETRY_INTERVAL: Duration = Duration::from_millis(500);
        let window_size = VersionedConstants::latest_constants().fee_proposal_window_size;
        let window_end_height = start_height.0;
        let window_start_height = window_end_height.saturating_sub(window_size);
        let mut pending_heights: VecDeque<BlockNumber> =
            (window_start_height..window_end_height).map(BlockNumber).collect();
        while let Some(block_number) = pending_heights.pop_front() {
            match self.deps.state_sync_client.get_block(block_number).await {
                Ok(block) => self.record_fee_proposal(
                    block_number,
                    block.block_header_without_hash.fee_proposal_fri,
                ),
```

**File:** crates/apollo_consensus_orchestrator/src/sequencer_consensus_context.rs (L1082-1082)
```rust
        self.record_fee_proposal(height, sync_block.block_header_without_hash.fee_proposal_fri);
```

**File:** crates/apollo_consensus_orchestrator/src/validate_proposal.rs (L396-416)
```rust
    // Validate fee_proposal is within the configured margin of fee_actual.
    // During initiation (fee_actual is None, <window_size blocks), bounds are not enforced.
    if let (Some(fee_actual), Some(fee_proposal)) =
        (proposal_init_validation.fee_actual, init_proposed.fee_proposal_fri)
    {
        let (lower_bound, upper_bound) = fee_proposal_bounds(
            fee_actual,
            VersionedConstants::latest_constants().fee_proposal_margin_ppt,
        );
        if fee_proposal.0 < lower_bound || fee_proposal.0 > upper_bound {
            return Err(ValidateProposalError::InvalidProposalInit(
                init_proposed.clone(),
                proposal_init_validation.clone(),
                format!(
                    "Fee proposal out of bounds: fee_actual={}, fee_proposal={}, allowed \
                     range=[{lower_bound}, {upper_bound}]",
                    fee_actual.0, fee_proposal.0
                ),
            ));
        }
    }
```
