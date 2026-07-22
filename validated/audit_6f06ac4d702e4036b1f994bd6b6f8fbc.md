### Title
`fee_proposal_margin_ppt` and `fee_proposal_window_size` Are Always Read from `latest_constants()` Regardless of the Block's `starknet_version`, Causing Proposer/Validator Divergence on Protocol Upgrade - (File: `crates/apollo_consensus_orchestrator/src/validate_proposal.rs`)

---

### Summary

Both the proposer and the validator unconditionally read `fee_proposal_margin_ppt` and `fee_proposal_window_size` from `VersionedConstants::latest_constants()` rather than from the constants keyed to the block's actual `starknet_version`. When a new Starknet version ships with changed values for either constant, nodes that have upgraded and nodes that have not yet upgraded will compute different `fee_actual` medians and different acceptance bounds for `fee_proposal_fri`, causing valid proposals from honest nodes running the old binary to be rejected by validators running the new binary (or vice versa), and causing the sequencer to accept or reject proposals based on an incorrect fee bound.

---

### Finding Description

The `apollo_versioned_constants` crate defines `VersionedConstants` with per-version JSON files for `fee_proposal_margin_ppt` and `fee_proposal_window_size`: [1](#0-0) 

The macro `define_versioned_constants` exposes both `latest_constants()` (always the newest version) and `get(version)` (version-keyed lookup): [2](#0-1) 

However, every call site in the consensus orchestrator that reads these two constants calls `latest_constants()` unconditionally, ignoring the `starknet_version` carried in the proposal:

**Proposer path** (`build_proposal`): [3](#0-2) [4](#0-3) 

**Validator path** (`is_proposal_init_valid`): [5](#0-4) 

**Window pruning** (also uses `latest_constants()`): [6](#0-5) 

**Window initialization**: [7](#0-6) 

The `starknet_version` field is present in `ProposalInit` and is already used to gate whether `fee_proposal_fri` is required at all (the `>= V0_14_3` check): [8](#0-7) 

But the version is never used to select which `fee_proposal_margin_ppt` or `fee_proposal_window_size` to apply when computing bounds or the sliding-window median.

The analog to the Sofa Protocol bug is exact: just as the Sofa fee rate is read at burn-time rather than being locked at mint-time, the sequencer reads the fee-proposal margin and window size at validation-time from the latest constants rather than from the constants that were in effect when the block's `starknet_version` was established.

---

### Impact Explanation

**Impact: High — RPC/gateway admission and consensus proposal validation return wrong values.**

If a future versioned-constants update changes `fee_proposal_margin_ppt` (e.g., from `2` to `5`) or `fee_proposal_window_size` (e.g., from `10` to `20`):

1. **Wrong `fee_actual` median**: `compute_fee_actual` is called with the new `window_size` but the window was populated under the old `window_size`. A validator running the new binary will look back 20 blocks; a proposer running the old binary computed its `fee_proposal` from a 10-block median. The two sides derive different `fee_actual` values.

2. **Wrong acceptance bounds**: `fee_proposal_bounds` is called with the new `margin_ppt`. A proposer that clamped its proposal to the old ±0.2% band will be rejected by a validator applying the new ±0.5% band (or vice versa).

3. **Consensus liveness failure**: Honest proposals from nodes running the old binary are rejected by validators running the new binary, or honest proposals from nodes running the new binary are rejected by validators running the old binary. This is a consensus-level admission failure matching the "Mempool/gateway/RPC admission accepts invalid transactions or rejects valid transactions" impact.

4. **Wrong fee committed to block**: If the proposer and validator happen to agree (both on new binary) but the `fee_actual` was computed with the wrong window size relative to the historical data, the `fee_proposal_fri` committed to the block header is economically wrong — matching the "Incorrect fee, gas, bouncer, resource accounting" impact.

---

### Likelihood Explanation

The likelihood is **medium**. All current versioned-constants JSON files happen to have identical values for `fee_proposal_margin_ppt` (2) and `fee_proposal_window_size` (10) across all versions (V0_14_0 through V0_14_4): [9](#0-8) [10](#0-9) 

So the bug is latent today. It becomes exploitable the moment a new version ships with a different value for either constant — a routine protocol upgrade. The `define_versioned_constants` macro is explicitly designed to allow per-version changes to these fields, so the invariant will eventually be violated.

---

### Recommendation

Replace every `VersionedConstants::latest_constants()` call that reads `fee_proposal_margin_ppt`, `fee_proposal_window_size`, or `l1_gas_price_margin_percent` in the consensus orchestrator with `VersionedConstants::get(&block_starknet_version)`, where `block_starknet_version` is the version carried in `ProposalInit::starknet_version` (for validation) or the current block's version (for proposal building).

Concretely:

- In `is_proposal_init_valid`, replace:
  ```rust
  VersionedConstants::latest_constants().fee_proposal_margin_ppt
  VersionedConstants::latest_constants().l1_gas_price_margin_percent
  ```
  with `VersionedConstants::get(&init_proposed.starknet_version)?.<field>`.

- In `compute_fee_actual` call sites (proposer and validator), pass `VersionedConstants::get(&block_version)?.fee_proposal_window_size` instead of `VersionedConstants::latest_constants().fee_proposal_window_size`.

- In `prune_fee_proposals_window` and `initialize_fee_proposals_window`, use the version of the block being processed rather than `latest_constants()`.

This locks the fee-proposal parameters to the version in effect when the block was proposed, exactly analogous to the Sofa recommendation of including the settlement fee in the product ID hash.

---

### Proof of Concept

Assume a future upgrade ships `orchestrator_versioned_constants_0_15_0.json` with `fee_proposal_margin_ppt: 5` (was `2`) and `fee_proposal_window_size: 20` (was `10`).

**Node A** (old binary, `latest` = V0_14_4):
- Computes `fee_actual` as median of last 10 blocks → e.g., `GasPrice(10_000_000_000)`.
- Computes bounds with `margin_ppt=2`: `[9_980_039_920, 10_020_000_000]`.
- Publishes `fee_proposal_fri = 10_010_000_000` (within old bounds).

**Node B** (new binary, `latest` = V0_15_0):
- Computes `fee_actual` as median of last 20 blocks → e.g., `GasPrice(9_500_000_000)` (different window).
- Computes bounds with `margin_ppt=5`: `[9_452_380_952, 9_547_619_048]`.
- Node A's proposal `10_010_000_000 > 9_547_619_048` → **rejected**.

The rejection path in `is_proposal_init_valid`: [11](#0-10) 

Node B rejects Node A's honest proposal with `"Fee proposal out of bounds"`, stalling consensus.

### Citations

**File:** crates/apollo_versioned_constants/src/lib.rs (L27-30)
```rust
    /// Number of `fee_proposal` values used to compute `fee_actual` (sliding window).
    pub fee_proposal_window_size: u64,
    /// Maximum `fee_proposal` change per block in parts per thousand (e.g., `2` = 0.2%).
    pub fee_proposal_margin_ppt: u128,
```

**File:** crates/starknet_api/src/versioned_constants_logic.rs (L189-205)
```rust
            fn latest_constants() -> &'static Self {
                Self::get(&starknet_api::block::StarknetVersion::LATEST)
                    .expect("Latest version should support VC.")
            }

            fn get(
                version: &starknet_api::block::StarknetVersion
            ) -> Result<&'static Self, Self::Error> {
                match version {
                    $(
                        starknet_api::block::StarknetVersion::$variant => Ok(
                            & $crate::paste::paste! { [<VERSIONED_CONSTANTS_ $variant:upper>] }
                        ),
                    )*
                    _ => Err(Self::Error::InvalidStarknetVersion(*version)),
                }
            }
```

**File:** crates/apollo_consensus_orchestrator/src/sequencer_consensus_context.rs (L312-314)
```rust
    fn prune_fee_proposals_window(&mut self, current_height: BlockNumber) {
        let window_size = VersionedConstants::latest_constants().fee_proposal_window_size;
        let cutoff = BlockNumber(current_height.0.saturating_sub(window_size));
```

**File:** crates/apollo_consensus_orchestrator/src/sequencer_consensus_context.rs (L331-331)
```rust
        let window_size = VersionedConstants::latest_constants().fee_proposal_window_size;
```

**File:** crates/apollo_consensus_orchestrator/src/sequencer_consensus_context.rs (L487-491)
```rust
        let proposal = compute_fee_proposal(
            fee_target,
            fee_actual,
            VersionedConstants::latest_constants().fee_proposal_margin_ppt,
        );
```

**File:** crates/apollo_consensus_orchestrator/src/sequencer_consensus_context.rs (L782-786)
```rust
        let fee_actual = compute_fee_actual(
            &self.fee_proposals_window,
            build_param.height,
            VersionedConstants::latest_constants().fee_proposal_window_size,
        );
```

**File:** crates/apollo_consensus_orchestrator/src/validate_proposal.rs (L370-394)
```rust
    // fee_proposal is required iff Starknet version >= V0_14_3.
    let fee_proposal_required = init_proposed.starknet_version >= StarknetVersion::V0_14_3;
    match (init_proposed.fee_proposal_fri, fee_proposal_required) {
        (Some(_), false) => {
            return Err(ValidateProposalError::InvalidProposalInit(
                init_proposed.clone(),
                proposal_init_validation.clone(),
                format!(
                    "fee_proposal must be absent before V0_14_3, got Some at version {}",
                    init_proposed.starknet_version
                ),
            ));
        }
        (None, true) => {
            return Err(ValidateProposalError::InvalidProposalInit(
                init_proposed.clone(),
                proposal_init_validation.clone(),
                format!(
                    "fee_proposal is required at V0_14_3+, got None at version {}",
                    init_proposed.starknet_version
                ),
            ));
        }
        _ => {}
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

**File:** crates/apollo_versioned_constants/resources/orchestrator_versioned_constants_0_14_0.json (L1-9)
```json
{
    "fee_proposal_margin_ppt": 2,
    "fee_proposal_window_size": 10,
    "gas_price_max_change_denominator": 48,
    "gas_target": 3200000000,
    "max_block_size": 4000000000,
    "min_gas_price": "0xb2d05e00",
    "l1_gas_price_margin_percent": 10
}
```

**File:** crates/apollo_versioned_constants/resources/orchestrator_versioned_constants_0_14_4.json (L1-9)
```json
{
    "fee_proposal_margin_ppt": 2,
    "fee_proposal_window_size": 10,
    "gas_price_max_change_denominator": 48,
    "gas_target": 1040000000,
    "max_block_size": 5800000000,
    "min_gas_price": "0x1dcd65000",
    "l1_gas_price_margin_percent": 10
}
```
