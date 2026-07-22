### Title
Unauthenticated `fee_proposal_fri` in P2P `SignedBlockHeader` Poisons `fee_proposals_window`, Causing Wrong `fee_actual` and Consensus Proposal Mis-validation — (`File: crates/apollo_p2p_sync/src/client/header.rs`, `crates/apollo_protobuf/src/proto/p2p/proto/sync/header.proto`)

---

### Summary

The `fee_proposal_fri` field in the P2P sync `SignedBlockHeader` message is explicitly documented as **not part of the block hash** and therefore not covered by the block signature. The P2P sync client writes this field directly to storage without any integrity check. The stored value is later read by `initialize_fee_proposals_window` to populate the `fee_proposals_window`, which drives `compute_fee_actual`. The resulting `fee_actual` is used by every validator node to bound-check the proposer's `fee_proposal_fri` in `validate_proposal`. A single malicious P2P peer can inject arbitrary `fee_proposal_fri` values into a syncing node's window, causing it to compute a wrong `fee_actual` and either reject every valid proposal or accept every invalid one.

---

### Finding Description

**The unverified field and its explicit acknowledgment**

The protobuf definition in `header.proto` carries a machine-readable warning:

```
// WARNING: this field is currently not part of the block hash, so the value must be trusted.
optional Uint128 fee_proposal_fri = 22;
```

The same comment appears in the generated Rust struct in `protoc_output.rs` and in `BlockHeaderWithoutHash` in `starknet_api/src/block.rs`:

```rust
// TODO(AndrewL): Add this field into the block hash.
pub fee_proposal_fri: Option<GasPrice>,
``` [1](#0-0) [2](#0-1) 

**The P2P sync path writes the field without verification**

`HeaderStreamBuilder::parse_data_for_block` validates only block number ordering and signature vector length. It does not verify `fee_proposal_fri` against the block hash or the block signature:

```rust
if block_number != signed_block_header.block_header.block_header_without_hash.block_number {
    return Err(...);
}
if signed_block_header.signatures.len() != ALLOWED_SIGNATURES_LENGTH {
    return Err(...);
}
Ok(Some(signed_block_header))
```

The `write_to_storage` implementation then calls `append_header`, which faithfully copies `fee_proposal_fri` from the wire message into the MDBX `headers` table:

```rust
fee_proposal_fri: block_header.block_header_without_hash.fee_proposal_fri,
``` [3](#0-2) [4](#0-3) 

**The stored value seeds the `fee_proposals_window`**

`initialize_fee_proposals_window` reads `fee_proposal_fri` from storage for the `[start_height - window_size, start_height)` range and records each value into the in-memory `fee_proposals_window`:

```rust
Ok(block) => self.record_fee_proposal(
    block_number,
    block.block_header_without_hash.fee_proposal_fri,
),
``` [5](#0-4) 

**The window drives `fee_actual`, which gates proposal validation**

`compute_fee_actual` computes the median of the window. Both the proposer path and the validator path call it:

```rust
let fee_actual = compute_fee_actual(
    &self.fee_proposals_window,
    init.height,
    VersionedConstants::latest_constants().fee_proposal_window_size,
);
```

`validate_proposal` then enforces that the proposer's `fee_proposal_fri` lies within `fee_proposal_bounds(fee_actual, margin_ppt)`. If `fee_actual` is wrong, the bounds are wrong:

```rust
if fee_proposal.0 < lower_bound || fee_proposal.0 > upper_bound {
    return Err(ValidateProposalError::InvalidProposalInit(...));
}
``` [6](#0-5) [7](#0-6) 

**The `try_sync` path has the same exposure**

When a node falls behind and calls `try_sync`, it also records `fee_proposal_fri` from the synced block directly into the window without any hash-binding check:

```rust
self.record_fee_proposal(height, sync_block.block_header_without_hash.fee_proposal_fri);
``` [8](#0-7) 

---

### Impact Explanation

A malicious P2P peer that serves `SignedBlockHeader` messages with crafted `fee_proposal_fri` values (e.g., `u128::MAX` or `0`) for the `window_size` most recent blocks will cause the victim node to compute a `fee_actual` of `u128::MAX` or `0`. The resulting `fee_proposal_bounds` will either be `[u128::MAX * (1+margin), u128::MAX]` (impossible to satisfy) or `[0, 0]` (only satisfiable by a zero proposal). Every legitimate proposer's `fee_proposal_fri` will fall outside these bounds, and the victim node will reject every valid proposal, effectively partitioning it from consensus. Conversely, setting all window entries to a value that makes the bounds `[0, u128::MAX]` causes the node to accept any `fee_proposal_fri`, including adversarially extreme values.

This matches: **High. Mempool/gateway/RPC admission accepts invalid transactions or rejects valid transactions before sequencing** (consensus proposal admission is the sequencer-side gate before sequencing).

---

### Likelihood Explanation

Any node that performs P2P sync (the standard catch-up path) is exposed. The attacker needs only to be a reachable P2P peer — no privileged role, no cryptographic material, no valid block production capability. The `fee_proposal_window_size` is a small integer (e.g., 10 blocks per the versioned constants), so the attacker needs to serve only `window_size` consecutive headers with manipulated values to fully corrupt the window. The block hash and block signature remain valid because `fee_proposal_fri` is explicitly excluded from both.

---

### Recommendation

1. **Include `fee_proposal_fri` in the block hash** — resolve the `TODO(AndrewL)` in `BlockHeaderWithoutHash`. Once the field is hash-committed, the existing block-hash verification in the P2P sync path will cover it.
2. **Until (1) is done, derive `fee_proposal_fri` from the consensus record** — the value is already committed in the `ProposalCommitment` via `proposal_commitment_from(partial, fee_proposal)` for V0_14_3+ blocks. The P2P sync client should verify the received `fee_proposal_fri` against the stored `ProposalCommitment` before writing it to storage.
3. **Reject `SignedBlockHeader` messages where `fee_proposal_fri` is present but the block's `starknet_version < V0_14_3`**, and vice versa, as a defense-in-depth check.

---

### Proof of Concept

1. Victim node starts P2P sync from block 0. Attacker is its only peer.
2. Attacker serves `window_size` (e.g., 10) consecutive `SignedBlockHeader` messages, each with a valid block hash and valid block signature, but with `fee_proposal_fri = Some(u128::MAX)`.
3. Victim's `initialize_fee_proposals_window` reads these headers from storage and populates `fee_proposals_window` with `u128::MAX` for all 10 heights.
4. `compute_fee_actual` returns `GasPrice(u128::MAX)`.
5. `fee_proposal_bounds(u128::MAX, margin_ppt)` returns `(lower ≈ u128::MAX, upper = u128::MAX)`.
6. Any legitimate proposer's `fee_proposal_fri` (e.g., `GasPrice(75_000_000_000)`) satisfies `75_000_000_000 < lower`, so `validate_proposal` returns `ValidateProposalError::InvalidProposalInit`.
7. The victim node votes NIL on every proposal and is effectively excluded from consensus. [9](#0-8) [10](#0-9) [11](#0-10)

### Citations

**File:** crates/apollo_protobuf/src/proto/p2p/proto/sync/header.proto (L34-36)
```text
    // Proposer's oracle-derived recommended fee. Absent for pre-V0_14_3 blocks.
    // WARNING: this field is currently not part of the block hash, so the value must be trusted.
    optional Uint128 fee_proposal_fri = 22;
```

**File:** crates/starknet_api/src/block.rs (L245-247)
```rust
    // TODO(AndrewL): Add this field into the block hash.
    /// Proposer's oracle-derived recommended L2 gas fee. `None` for pre-V0_14_3 blocks.
    pub fee_proposal_fri: Option<GasPrice>,
```

**File:** crates/apollo_p2p_sync/src/client/header.rs (L26-71)
```rust
impl BlockData for SignedBlockHeader {
    #[allow(clippy::as_conversions)] // FIXME: use int metrics so `as f64` may be removed.
    fn write_to_storage<'a>(
        self: Box<Self>,
        storage_writer: &'a mut StorageWriter,
        _class_manager_client: &'a mut SharedClassManagerClient,
    ) -> BoxFuture<'a, Result<(), P2pSyncClientError>> {
        async move {
            storage_writer
                .begin_rw_txn()?
                .append_header(
                    self.block_header.block_header_without_hash.block_number,
                    &self.block_header,
                )?
                .append_block_signature(
                    self.block_header.block_header_without_hash.block_number,
                    self
                    .signatures
                    // In the future we will support multiple signatures.
                    .first()
                    // The verification that the size of the vector is 1 is done in the data
                    // verification.
                    .expect("Vec::first should return a value on a vector of size 1"),
                )?
                .commit()?;
            STATE_SYNC_HEADER_MARKER.set_lossy(
                self.block_header.block_header_without_hash.block_number.unchecked_next().0,
            );
            // TODO(shahak): Fix code dup with central sync
            let time_delta = Utc::now()
                - Utc
                    .timestamp_opt(
                        self.block_header.block_header_without_hash.timestamp.0 as i64,
                        0,
                    )
                    .single()
                    .expect("block timestamp should be valid");
            let header_latency = time_delta.num_seconds();
            debug!("Header latency: {}.", header_latency);
            if header_latency >= 0 {
                STATE_SYNC_HEADER_LATENCY_SEC.set_lossy(header_latency);
            }
            Ok(())
        }
        .boxed()
    }
```

**File:** crates/apollo_p2p_sync/src/client/header.rs (L82-121)
```rust
    fn parse_data_for_block<'a>(
        signed_headers_response_manager: &'a mut ClientResponsesManager<
            DataOrFin<SignedBlockHeader>,
        >,
        block_number: BlockNumber,
        _storage_reader: &'a StorageReader,
    ) -> BoxFuture<'a, Result<Option<Self::Output>, ParseDataError>> {
        async move {
            // TODO(noamsp): investigate and remove this timeout.
            let maybe_signed_header =
                timeout(Duration::from_secs(15), signed_headers_response_manager.next())
                    .await
                    .ok()
                    .flatten()
                    .ok_or(ParseDataError::BadPeer(BadPeerError::SessionEndedWithoutFin {
                        type_description: Self::TYPE_DESCRIPTION,
                    }))?;
            let Some(signed_block_header) = maybe_signed_header?.0 else {
                return Ok(None);
            };
            // TODO(shahak): Check that parent_hash is the same as the previous block's hash
            // and handle reverts.
            if block_number
                != signed_block_header.block_header.block_header_without_hash.block_number
            {
                return Err(ParseDataError::BadPeer(BadPeerError::HeadersUnordered {
                    expected_block_number: block_number,
                    actual_block_number: signed_block_header
                        .block_header
                        .block_header_without_hash
                        .block_number,
                }));
            }
            if signed_block_header.signatures.len() != ALLOWED_SIGNATURES_LENGTH {
                return Err(ParseDataError::BadPeer(BadPeerError::WrongSignaturesLength {
                    signatures: signed_block_header.signatures,
                }));
            }
            Ok(Some(signed_block_header))
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

**File:** crates/apollo_consensus_orchestrator/src/sequencer_consensus_context.rs (L326-354)
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
                Err(StateSyncClientError::StateSyncError(StateSyncError::BlockNotFound(_))) => {
                    warn!(
                        "State sync not ready for height {block_number}; re-queueing after \
                         {STATE_SYNC_RETRY_INTERVAL:?}"
                    );
                    pending_heights.push_back(block_number);
                    tokio::time::sleep(STATE_SYNC_RETRY_INTERVAL).await;
                }
                Err(e) => return Err(e),
            }
        }
        Ok(())
    }
```

**File:** crates/apollo_consensus_orchestrator/src/sequencer_consensus_context.rs (L1082-1082)
```rust
        self.record_fee_proposal(height, sync_block.block_header_without_hash.fee_proposal_fri);
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

**File:** crates/apollo_consensus_orchestrator/src/dynamic_gas_price/mod.rs (L144-151)
```rust
pub(crate) fn fee_proposal_bounds(fee_actual: GasPrice, margin_ppt: u128) -> (u128, u128) {
    let denom = U256::from(PPT_DENOMINATOR);
    let scaled = denom + U256::from(margin_ppt);
    let fee_actual_u256 = U256::from(fee_actual.0);
    let upper = u128::try_from(fee_actual_u256 * scaled / denom).unwrap_or(u128::MAX);
    let lower = u128::try_from(fee_actual_u256 * denom / scaled).unwrap_or(0);
    (lower, upper)
}
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

**File:** crates/apollo_protobuf/src/protobuf/protoc_output.rs (L1223-1228)
```rust
    /// Proposer's oracle-derived recommended fee. Absent for pre-V0_14_3 blocks.
    /// WARNING: this field is currently not part of the block hash, so the value must be trusted.
    ///
    /// can be more explicit here about the signature structure as this is not part of account abstraction
    #[prost(message, optional, tag = "22")]
    pub fee_proposal_fri: ::core::option::Option<Uint128>,
```
