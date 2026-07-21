Based on my investigation of the codebase, here is the analysis:

## Key Facts Established

**1. `fee_proposal_fri` is confirmed absent from `PartialBlockHashComponents`**

The struct definition contains only `header_commitments`, `block_number`, `l1_gas_price`, `l1_data_gas_price`, `l2_gas_price`, `sequencer`, `timestamp`, and `starknet_version` — `fee_proposal_fri` is not present. [1](#0-0) 

The `calculate_block_hash` function chains only those fields; `fee_proposal_fri` is never chained. [2](#0-1) 

The TODO comment in `gas_prices_to_hash` explicitly acknowledges this: `// TODO(Ayelet): add l2_gas_consumed, next_l2_gas_price after 0.14.0.` — and `fee_proposal_fri` is not even mentioned there. [3](#0-2) 

**2. `fee_proposal_fri` IS stored in `StorageBlockHeader` and round-tripped through P2P protobuf**

`StorageBlockHeader` has the field at line 113, and `append_header` stores it verbatim from the incoming `BlockHeader`. [4](#0-3) [5](#0-4) 

The protobuf converter accepts `fee_proposal_fri` from the wire with no validation against the block hash. [6](#0-5) 

**3. The critical guard: `proposal_commitment_from` binds `fee_proposal_fri` — but only during consensus, not P2P sync**

For V0_14_3+ blocks, `fee_proposal_fri` is bound to the `ProposalCommitment` via `Poseidon(partial.0, fee_proposal_fri)`. This prevents equivocation *during live consensus*. [7](#0-6) 

During proposal validation, `fee_proposal_fri` is also range-checked against `fee_actual` bounds. [8](#0-7) 

**4. The P2P sync path has no equivalent guard**

The P2P sync `header.rs` converter accepts `fee_proposal_fri` from the wire and places it directly into `BlockHeaderWithoutHash`. The block hash verification (which P2P sync does perform) cannot catch a tampered `fee_proposal_fri` because the block hash does not cover it. The `ProposalCommitment` is a consensus-time artifact that is not stored in the block header and is not re-verified during P2P sync.

**5. `fee_proposal_fri` from stored blocks feeds `compute_fee_actual`**

`compute_fee_actual` takes a `BTreeMap<BlockNumber, Option<GasPrice>>` populated from storage. A manipulated `fee_proposal_fri` in storage would shift the median, altering `fee_actual` for future blocks, which in turn widens or narrows the bounds check on future `fee_proposal_fri` values in consensus. [9](#0-8) 

---

## Conclusion

The `proposal_commitment_from` guard is the decisive protection. It cryptographically binds `fee_proposal_fri` to the commitment that consensus validators sign over, meaning **no honest consensus participant can be made to accept a block with a forged `fee_proposal_fri` through the consensus path**.

However, the question's attack vector is the **P2P sync path**, not the consensus path. A syncing node that receives a `SignedBlockHeader` from a malicious peer will:
- Accept an arbitrary `fee_proposal_fri` (block hash verification passes because the field is not hash-bound)
- Store it in `StorageBlockHeader`
- Serve it via RPC as an authoritative block header field
- Potentially use it to populate the `fee_proposals_window` for `compute_fee_actual`

The P2P sync path does not re-derive or verify `fee_proposal_fri` against the `ProposalCommitment` (which is not stored in the block header at all). This is a real structural gap.

**However**, the impact on the syncing node's own consensus behavior depends on whether `fee_proposals_window` is populated from P2P-synced storage or only from locally-witnessed consensus rounds. I was unable to fully read `sequencer_consensus_context.rs` to confirm this. If the window is populated from storage (which is the natural implementation), the attack reaches `compute_fee_actual`. If the window is populated only from live consensus observations, the impact is limited to RPC serving a wrong value.

Given the explicit TODO ("Add this field into the block hash"), the design acknowledges the gap. The `ProposalCommitment` binding is the intended interim guard, but it only covers the consensus path, not the P2P sync path.

---

### Title
`fee_proposal_fri` not hash-bound: P2P sync accepts attacker-injected value stored as authoritative — (`crates/apollo_storage/src/header.rs`, `crates/apollo_protobuf/src/converters/header.rs`)

### Summary
A malicious P2P peer can advertise a `SignedBlockHeader` with an arbitrary `fee_proposal_fri` value. Because `fee_proposal_fri` is excluded from `calculate_block_hash` and `PartialBlockHashComponents`, block hash verification passes. The value is stored in `StorageBlockHeader` and served via RPC as authoritative. The `ProposalCommitment` binding that protects the consensus path is not re-verified during P2P sync.

### Finding Description
`fee_proposal_fri` is transmitted in protobuf field 22 of `SignedBlockHeader` and stored verbatim in `StorageBlockHeader.fee_proposal_fri`. The block hash (`calculate_block_hash`) does not include this field (acknowledged by a TODO comment). The only binding is `proposal_commitment_from`, which computes `Poseidon(partial_block_hash, fee_proposal_fri)` for V0_14_3+ blocks — but this `ProposalCommitment` is a consensus-time value that is not stored in the block header and is not re-verified when a syncing node ingests a `SignedBlockHeader` via P2P sync. The P2P protobuf converter accepts any `fee_proposal_fri` value without validation. [6](#0-5) [5](#0-4) 

### Impact Explanation
- The node stores and serves an authoritative-looking `fee_proposal_fri` that was never committed to in the block hash, violating the invariant that all RPC-served block header fields are hash-bound.
- If `fee_proposals_window` is populated from storage (the natural path), a manipulated `fee_proposal_fri` shifts the median in `compute_fee_actual`, altering `fee_actual` for future blocks and widening/narrowing the bounds check on future `fee_proposal_fri` values in consensus.
- Impact: **High** — RPC serves an authoritative-looking wrong value; potential downstream effect on fee computation.

### Likelihood Explanation
Any unprivileged P2P peer can send a `SignedBlockHeader` with a valid block hash and signatures but an arbitrary `fee_proposal_fri`. The attack requires only network access to a syncing node.

### Recommendation
Either:
1. Include `fee_proposal_fri` in `calculate_block_hash` (the TODO already tracks this), or
2. Store the `ProposalCommitment` alongside the block header and re-verify `fee_proposal_fri` against it during P2P sync ingestion.

### Proof of Concept
Build two `SignedBlockHeader` values identical except for `fee_proposal_fri` (e.g., `None` vs `Some(GasPrice(u128::MAX))`). Call `calculate_block_hash` on both via `PartialBlockHashComponents::new`. Assert the two hashes are equal — confirming `fee_proposal_fri` is not hash-bound and a P2P peer can freely vary it while keeping the block hash valid. [10](#0-9) [7](#0-6)

### Citations

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

**File:** crates/starknet_api/src/block_hash/block_hash_calculator.rs (L416-416)
```rust
// TODO(Ayelet): add l2_gas_consumed, next_l2_gas_price after 0.14.0.
```

**File:** crates/apollo_storage/src/header.rs (L112-114)
```rust
    /// Proposer's oracle-derived recommended L2 gas fee. `None` for pre-V0_14_3 blocks.
    pub fee_proposal_fri: Option<GasPrice>,
}
```

**File:** crates/apollo_storage/src/header.rs (L308-329)
```rust
        let storage_block_header = StorageBlockHeader {
            block_hash: block_header.block_hash,
            parent_hash: block_header.block_header_without_hash.parent_hash,
            block_number: block_header.block_header_without_hash.block_number,
            l1_gas_price: block_header.block_header_without_hash.l1_gas_price,
            l1_data_gas_price: block_header.block_header_without_hash.l1_data_gas_price,
            l2_gas_price: block_header.block_header_without_hash.l2_gas_price,
            l2_gas_consumed: block_header.block_header_without_hash.l2_gas_consumed,
            next_l2_gas_price: block_header.block_header_without_hash.next_l2_gas_price,
            state_root: block_header.block_header_without_hash.state_root,
            sequencer: block_header.block_header_without_hash.sequencer,
            timestamp: block_header.block_header_without_hash.timestamp,
            l1_da_mode: block_header.block_header_without_hash.l1_da_mode,
            state_diff_commitment: block_header.state_diff_commitment,
            transaction_commitment: block_header.transaction_commitment,
            event_commitment: block_header.event_commitment,
            receipt_commitment: block_header.receipt_commitment,
            state_diff_length: block_header.state_diff_length,
            n_transactions: block_header.n_transactions,
            n_events: block_header.n_events,
            fee_proposal_fri: block_header.block_header_without_hash.fee_proposal_fri,
        };
```

**File:** crates/apollo_protobuf/src/converters/header.rs (L179-179)
```rust
        let fee_proposal_fri = value.fee_proposal_fri.map(|v| GasPrice(u128::from(v)));
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

**File:** crates/apollo_consensus_orchestrator/src/validate_proposal.rs (L398-416)
```rust
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
