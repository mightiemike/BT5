### Title
`ExecutionStatus::Unknown` Semantic Overloading — Two Distinct States Collapsed into One Borsh Discriminant — (`core/primitives/src/transaction.rs`, `chain/chain/src/chain.rs`)

---

### Summary

`ExecutionStatus::Unknown = 0` (Borsh discriminant `0x00`) is stored in `DBCol::TransactionResultForBlock` and exposed via the RPC API with two entirely different semantic meanings. The disambiguation requires an out-of-band side-channel (`num_outcomes` count), not the stored byte itself. This is the exact nearcore analog of the Audius `No`-outcome semantic overload.

---

### Finding Description

`ExecutionStatus` is the on-chain, Borsh-serialized status of every transaction and receipt outcome. Its definition explicitly documents the overload in the comment:

```rust
pub enum ExecutionStatus {
    /// The execution is pending or unknown.
    #[default]
    Unknown = 0,
    ...
}
``` [1](#0-0) 

The same variant `Unknown = 0` is written to `DBCol::TransactionResultForBlock` in two distinct situations:

1. **"Not started"** — the transaction has been included in a block but has not yet been converted to a receipt. There is exactly one outcome record, and it carries `Unknown`.
2. **"Started / pending"** — the transaction has been converted to a receipt, but the receipt has not yet been executed. There are multiple outcome records; the transaction's own record carries `Unknown`.

The disambiguation is performed entirely by a side-channel — the count of outcomes — inside `get_execution_status`:

```rust
ExecutionStatusView::Unknown if num_outcomes == 1 => {
    Some(FinalExecutionStatus::NotStarted)
}
ExecutionStatusView::Unknown => Some(FinalExecutionStatus::Started),
``` [2](#0-1) 

The same overloaded byte is re-used in `get_tx_execution_status` to mean "receipt not yet executed":

```rust
if outcome.outcome.status == ExecutionStatusView::Unknown {
    None  // treated as not-yet-executed
}
``` [3](#0-2) 

And again to mean "transaction not yet processed at all":

```rust
if execution_outcome.transaction_outcome.outcome.status == ExecutionStatusView::Unknown {
    return Ok(TxExecutionStatus::None);
}
``` [4](#0-3) 

The public RPC type `ExecutionStatusView` mirrors the same overloaded variant:

```rust
pub enum ExecutionStatusView {
    /// The execution is pending or unknown.
    Unknown = 0,
    ...
}
``` [5](#0-4) 

The `PartialExecutionStatus` used in Merkle-proof hashing also carries the same overloaded `Unknown = 0`: [6](#0-5) 

---

### Impact Explanation

**Protocol / DB layer:** Borsh byte `0x00` stored in `DBCol::TransactionResultForBlock` is ambiguous. Any reader of that column — indexers, archival nodes, light clients, future runtime code — that does not also know the total outcome count for the same transaction will misclassify the state. "Not started" and "started but pending" are operationally different: one means the transaction has not yet been applied to any block, the other means it has been applied and a receipt is in flight.

**RPC layer:** The JSON field `"Unknown"` returned by `tx` / `EXPERIMENTAL_tx_status` carries the same ambiguity to every external caller. Wallets and indexers that branch on `"Unknown"` cannot determine whether to retry submission (not started) or wait for receipt execution (started).

**Regression surface:** The disambiguation logic in `get_execution_status` is a fragile, implicit contract: it works only because `num_outcomes == 1` happens to correlate with "not started." Any future code path that reads `ExecutionStatus::Unknown` from the DB without reconstructing the full outcome list will silently misinterpret the state, exactly the regression risk the Audius report identified.

**Severity: Medium.** The current production code correctly disambiguates via the side-channel, so no immediate consensus divergence occurs. However, the semantic overload is baked into the stable Borsh schema (`ProtocolSchema`-annotated, discriminant `0x00`), making it a permanent protocol-level ambiguity that every future consumer must independently re-implement the same fragile side-channel to handle correctly.

---

### Likelihood Explanation

The overload is already exercised on every transaction query. Any new consumer of `DBCol::TransactionResultForBlock` or any new RPC handler that reads `ExecutionStatus` without replicating the `num_outcomes` side-channel will silently produce wrong results. The likelihood of a regression is proportional to the rate of new code touching execution outcomes.

---

### Recommendation

Add a dedicated `Pending` variant to `ExecutionStatus` (and its view/partial mirrors) with a new Borsh discriminant (e.g., `= 4`), distinct from `Unknown`. Assign `Unknown` the sole meaning of "genuinely unknown / uninitialized default" and `Pending` the meaning of "execution in progress." Migrate the write sites in the runtime and chain to emit `Pending` when a transaction has been converted to a receipt but the receipt has not yet executed. Update `get_execution_status` and `get_tx_execution_status` to match on `Pending` instead of using the `num_outcomes` side-channel. Because `ExecutionStatus` is `ProtocolSchema`-tracked and Borsh-stable, the new variant must be introduced under a `ProtocolFeature` gate to preserve backward compatibility with stored outcomes from older protocol versions.

---

### Proof of Concept

1. Submit any transaction to a NEAR node.
2. Query `tx` RPC immediately after inclusion but before the receipt executes.
3. Observe `status: "Unknown"` in the response — this means "started/pending."
4. Query `tx` RPC for a transaction hash that has never been submitted.
5. Observe `status: "Unknown"` again — this means "not started."
6. Both states produce identical Borsh bytes (`0x00`) in `DBCol::TransactionResultForBlock`; the only distinguishing information is the count of sibling outcome records, which is not encoded in the stored value itself.

The disambiguation code at `chain/chain/src/chain.rs:3028–3031` confirms the side-channel is load-bearing: removing or misapplying the `num_outcomes == 1` guard swaps the two meanings silently. [7](#0-6)

### Citations

**File:** core/primitives/src/transaction.rs (L543-546)
```rust
pub enum ExecutionStatus {
    /// The execution is pending or unknown.
    #[default]
    Unknown = 0,
```

**File:** core/primitives/src/transaction.rs (L597-602)
```rust
pub enum PartialExecutionStatus {
    Unknown = 0,
    Failure = 1,
    SuccessValue(Vec<u8>) = 2,
    SuccessReceiptId(CryptoHash) = 3,
}
```

**File:** chain/chain/src/chain.rs (L3022-3047)
```rust
        let num_outcomes = outcomes.len();
        outcomes
            .iter()
            .find_map(|outcome_with_id| {
                if outcome_with_id.id == looking_for_id {
                    match &outcome_with_id.outcome.status {
                        ExecutionStatusView::Unknown if num_outcomes == 1 => {
                            Some(FinalExecutionStatus::NotStarted)
                        }
                        ExecutionStatusView::Unknown => Some(FinalExecutionStatus::Started),
                        ExecutionStatusView::Failure(e) => {
                            Some(FinalExecutionStatus::Failure(e.clone()))
                        }
                        ExecutionStatusView::SuccessValue(v) => {
                            Some(FinalExecutionStatus::SuccessValue(v.clone()))
                        }
                        ExecutionStatusView::SuccessReceiptId(id) => {
                            looking_for_id = *id;
                            None
                        }
                    }
                } else {
                    None
                }
            })
            .unwrap_or(FinalExecutionStatus::Started)
```

**File:** chain/client/src/view_client_actor.rs (L522-524)
```rust
        if execution_outcome.transaction_outcome.outcome.status == ExecutionStatusView::Unknown {
            return Ok(TxExecutionStatus::None);
        }
```

**File:** chain/client/src/view_client_actor.rs (L539-544)
```rust
                if outcome.outcome.status == ExecutionStatusView::Unknown {
                    None
                } else {
                    Some(&outcome.id)
                }
            })
```

**File:** core/primitives/src/views.rs (L1806-1808)
```rust
pub enum ExecutionStatusView {
    /// The execution is pending or unknown.
    Unknown = 0,
```
