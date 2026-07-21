### Title
`fee_proposal_fri` stored in `BlockHeaderWithoutHash` but excluded from `calculate_block_hash` — canonical block hash does not commit to the fee-market signal (`crates/starknet_api/src/block_hash/block_hash_calculator.rs`)

---

### Summary

`fee_proposal_fri` (the proposer's oracle-derived L2 gas fee recommendation, introduced in Starknet V0_14_3) is stored in `BlockHeaderWithoutHash` and `StorageBlockHeader`, and is correctly bound into the consensus-layer `proposal_commitment_from` hash. However, it is explicitly absent from the canonical `calculate_block_hash` function — a `TODO` comment in the source acknowledges this gap. Any component that reads `fee_proposal_fri` from storage to compute `fee_actual` (and thus the next block's L2 gas price) relies on a value that is not verifiable from the canonical block hash. A malicious P2P peer can inject a wrong `fee_proposal_fri` during header sync, silently corrupting the fee-market window and causing the node to compute and serve wrong L2 gas prices.

---

### Finding Description

**Accumulation side — value is stored:**

`BlockHeaderWithoutHash` carries `fee_proposal_fri` with an explicit TODO acknowledging it is not yet in the block hash:

```rust
// TODO(AndrewL): Add this field into the block hash.
/// Proposer's oracle-derived recommended L2 gas fee. `None` for pre-V0_14_3 blocks.
pub fee_proposal_fri: Option<GasPrice>,
``` [1](#0-0) 

`StorageBlockHeader` mirrors this field: [2](#0-1) 

**Distribution side — canonical hash omits the value:**

`gas_prices_to_hash`, the only gas-price contribution to `calculate_block_hash`, hashes only `l1_gas_price`, `l1_data_gas_price`, and `l2_gas_price`. It carries a TODO to add `l2_gas_consumed` and `next_l2_gas_price` after 0.14.0, and `fee_proposal_fri` is absent entirely:

```rust
// TODO(Ayelet): add l2_gas_consumed, next_l2_gas_price after 0.14.0.
pub fn gas_prices_to_hash(
    l1_gas_price: &GasPricePerToken,
    l1_data_gas_price: &GasPricePerToken,
    l2_gas_price: &GasPricePerToken,
    block_hash_version: &BlockHashVersion,
) -> Vec<Felt> {
``` [3](#0-2) 

`calculate_block_hash` chains only the output of `gas_prices_to_hash` — `fee_proposal_fri` is never fed in: [4](#0-3) 

**Contrast — consensus commitment correctly binds the value:**

`proposal_commitment_from` does include `fee_proposal_fri` at the consensus layer:

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
``` [5](#0-4) 

So `fee_proposal_fri` is bound in the consensus commitment (what validators sign) but **not** in the canonical block hash (what SNOS proofs and L1 verify).

**Downstream economic use of the unverified value:**

`fee_proposals_window` is populated from `init.fee_proposal_fri` during live consensus. On restart, a node reconstructs this window by reading `fee_proposal_fri` from synced `BlockHeaderWithoutHash` records. `compute_fee_actual` then computes the median of the window: [6](#0-5) 

`fee_actual` feeds directly into `calculate_next_l2_gas_price_for_fin`, which sets the next block's L2 gas price: [7](#0-6) 

**P2P sync path delivers `fee_proposal_fri` without canonical verification:**

The protobuf `SignedBlockHeader` converter deserializes `fee_proposal_fri` from the wire and stores it directly into the block header: [8](#0-7) 

Because `fee_proposal_fri` is not part of the canonical block hash, a receiving node cannot verify this field against the hash it already trusts. A malicious peer can supply any value.

---

### Impact Explanation

**High — RPC fee estimation returns an authoritative-looking wrong value.**

A node that syncs headers from a malicious P2P peer receives crafted `fee_proposal_fri` values. These corrupt the `fee_proposals_window`. `compute_fee_actual` returns a wrong median, `calculate_next_l2_gas_price_for_fin` computes a wrong L2 gas price, and every subsequent `starknet_estimateFee` / `starknet_getBlockWithTxs` call on that node returns a wrong `l2_gas_price` and wrong fee estimates. Users submitting transactions based on these estimates will either overpay or have transactions rejected.

---

### Likelihood Explanation

**Medium.** The trigger requires a malicious P2P peer (or a compromised central sync source) to supply wrong `fee_proposal_fri` values in `SignedBlockHeader` messages. This is an unprivileged network-level action — no special key or sequencer role is needed to serve headers over P2P. The window size (`fee_proposal_window_size`) means only a sustained attack over multiple blocks is needed to shift `fee_actual` significantly.

---

### Recommendation

1. Add `fee_proposal_fri` to `calculate_block_hash` by introducing a new `BlockHashVersion` (e.g., `V0_14_3`) that includes it in the `gas_prices_to_hash` output or as a separate chained field.
2. Update `TryFrom<StarknetVersion> for BlockHashVersion` to map `>= V0_14_3` to the new version.
3. Until the block hash is updated, nodes reconstructing `fee_proposals_window` from storage after restart should cross-check `fee_proposal_fri` against the consensus `proposal_commitment` stored alongside the block, rather than trusting the raw header field.

---

### Proof of Concept

1. Node A is live and has a correct `fee_proposals_window` built from live consensus.
2. Node A restarts. It re-syncs block headers via P2P from Node B (malicious).
3. Node B serves valid block hashes (matching L1) but with `fee_proposal_fri` set to `u128::MAX` in each `SignedBlockHeader`.
4. Node A cannot detect the forgery: `fee_proposal_fri` is not part of the block hash it verifies.
5. Node A's `fee_proposals_window` fills with `u128::MAX` values.
6. `compute_fee_actual` returns `u128::MAX`; `calculate_next_l2_gas_price_for_fin` clamps to `u128::MAX`.
7. Node A's RPC returns `l2_gas_price = u128::MAX` in fee estimates and block headers — every transaction appears to require an astronomically high fee, effectively DoS-ing the node's fee estimation service.

The root cause is the split between the consensus commitment (which binds `fee_proposal_fri`) and the canonical block hash (which does not), exactly mirroring the external report's pattern of a value accumulated in one account but excluded from the only distribution mechanism.

### Citations

**File:** crates/starknet_api/src/block.rs (L245-247)
```rust
    // TODO(AndrewL): Add this field into the block hash.
    /// Proposer's oracle-derived recommended L2 gas fee. `None` for pre-V0_14_3 blocks.
    pub fee_proposal_fri: Option<GasPrice>,
```

**File:** crates/apollo_storage/src/header.rs (L112-113)
```rust
    /// Proposer's oracle-derived recommended L2 gas fee. `None` for pre-V0_14_3 blocks.
    pub fee_proposal_fri: Option<GasPrice>,
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

**File:** crates/apollo_consensus_orchestrator/src/dynamic_gas_price/mod.rs (L56-91)
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

**File:** crates/apollo_protobuf/src/converters/header.rs (L179-179)
```rust
        let fee_proposal_fri = value.fee_proposal_fri.map(|v| GasPrice(u128::from(v)));
```
