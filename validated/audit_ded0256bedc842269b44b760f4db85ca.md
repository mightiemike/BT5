### Title
`PromiseResume` Receipts Contribute Zero Gas to Congestion Accounting Despite Triggering Real Callback Execution — (`File: runtime/runtime/src/congestion_control.rs`)

### Summary

`compute_receipt_congestion_gas` unconditionally returns `Gas::ZERO` for `VersionedReceiptEnum::PromiseResume` receipts. Unlike `PromiseYield` (which is confined to a single account and never crosses shard boundaries), `PromiseResume` receipts can and do end up in the delayed receipts queue and in cross-shard outgoing buffers. When a `PromiseResume` is executed it immediately triggers the stored `PromiseYield` callback, which carries up to `max_total_prepaid_gas` (300 Tgas) of real work. Because the congestion accounting sees zero gas for every `PromiseResume` in the queue, the shard's `delayed_receipts_gas` and `buffered_receipts_gas` counters are systematically under-reported, allowing the congestion control to accept more inbound load than the shard can actually drain.

### Finding Description

`compute_receipt_congestion_gas` is the single function that determines how much gas a queued receipt "weighs" for congestion purposes. It is called both when a receipt is pushed into the delayed queue (`DelayedReceiptQueueWrapper::push`) and when a receipt is placed in an outgoing buffer (`ReceiptSinkV2WithInfo::forward_or_buffer_receipt`). The returned value is stored in `CongestionInfo::delayed_receipts_gas` and `CongestionInfo::buffered_receipts_gas`, which drive every congestion-control decision: whether to accept new transactions, how many receipts to forward to a remote shard, and whether to apply backpressure.

For `PromiseResume` the function returns `Gas::ZERO`:

```rust
VersionedReceiptEnum::PromiseResume(_) => {
    // The congestion control MVP does not account for resuming a promise.
    // Unlike `PromiseYield`, it is possible that a promise-resume ends
    // up in the delayed receipts queue.
    // But similar to a data receipt, it would be difficult to find the cost
    // of it without expensive state lookups.
    Ok(Gas::ZERO)
}
``` [1](#0-0) 

The comment itself acknowledges the gap: unlike `PromiseYield`, a `PromiseResume` **can** reach the delayed queue. When it is eventually dequeued and processed, the runtime immediately looks up and executes the matching `PromiseYield` callback receipt:

```rust
VersionedReceiptEnum::PromiseResume(data_receipt) => {
    ...
    return self.apply_action_receipt(..., &yield_receipt, ...).map(Some);
}
``` [2](#0-1) 

That callback is a full `ActionReceipt` with prepaid function-call gas (up to 300 Tgas). The congestion system never counted that gas when the `PromiseResume` entered the queue, so the shard's `delayed_receipts_gas` counter is permanently under-reported for the duration the receipt sits in the queue.

The same zero-gas path is taken when the `PromiseResume` is placed in a cross-shard outgoing buffer:

```rust
let gas = compute_receipt_congestion_gas(&receipt, &apply_state.config)?;
...
self.sink.buffer_receipt(receipt, size, gas, ...)?;
``` [3](#0-2) 

So `buffered_receipts_gas` is also zero for every buffered `PromiseResume`, meaning the sending shard's outgoing congestion level is also under-reported.

**Attack path (unprivileged user):**

1. Attacker deploys a contract on account A (shard X) that calls `promise_yield_create` with a high-gas callback (e.g. 200 Tgas).
2. Attacker calls `promise_yield_resume` from account B (shard Y) for each pending yield. Each call costs normal function-call gas on shard Y and emits a `PromiseResume` receipt destined for shard X.
3. Because `compute_receipt_congestion_gas` returns `Gas::ZERO` for `PromiseResume`, shard Y's outgoing buffer for shard X reports zero gas, so the forwarding limit is never hit regardless of how many such receipts are queued.
4. On shard X, each `PromiseResume` that overflows into the delayed queue also contributes zero to `delayed_receipts_gas`. The congestion level stays artificially low, so shard X continues accepting new transactions even though its delayed queue holds hundreds of Tgas of pending callback work.
5. When the delayed queue is eventually drained, each `PromiseResume` triggers its 200 Tgas callback, saturating the shard far beyond what the congestion signal predicted.

The `receipt_bytes` dimension of congestion **does** count `PromiseResume` size, so memory congestion provides partial protection. However, the primary congestion signal (`delayed_receipts_gas`) is the one used to throttle transaction acceptance and cross-shard forwarding, and it is zero for all `PromiseResume` receipts.

### Impact Explanation

The congestion control invariant is that `delayed_receipts_gas` accurately reflects the gas that will be burned draining the delayed queue. For `PromiseResume` this invariant is broken: the actual gas burned per receipt is up to 300 Tgas, but the reported gas is 0. An attacker who fills the delayed queue with N `PromiseResume` receipts causes the shard to underestimate its backlog by up to `N × 300 Tgas`. This allows the shard to keep accepting new transactions and forwarding receipts at full rate while it is actually overloaded, degrading throughput and increasing latency for all users on that shard. In a multi-shard environment this can cascade: a congested shard that appears uncongested will not trigger backpressure on sending shards, so the entire pipeline continues feeding work into an already-saturated shard.

### Likelihood Explanation

`PromiseYield`/`PromiseResume` is a production feature available to any contract author. Creating yield pairs costs normal gas, but the congestion underestimation scales with the callback gas, not the cost of the `promise_yield_resume` call itself. A moderately funded attacker can create a large number of high-gas-callback yields cheaply relative to the disruption caused. The attack is most effective during periods of existing load, when the delayed queue is already non-empty and the congestion signal is already close to the threshold.

### Recommendation

When a `PromiseResume` receipt is pushed into the delayed queue or an outgoing buffer, attribute to it the gas of the `PromiseYield` callback it will trigger. The callback receipt is stored in state keyed by `data_id`; the `PromiseResume` carries that `data_id`. Reading the callback's prepaid gas at push time (once, before the receipt is serialised into the queue) avoids repeated lookups and keeps the accounting in the same place as the existing `action_receipt_congestion_gas` path. Alternatively, store the callback's prepaid gas in the `PromiseResume` receipt itself at creation time so no state lookup is needed.

### Proof of Concept

```
1. Deploy contract on account "yield_account" (shard 0):
   - method "create_yield": calls promise_yield_create with 200 Tgas callback

2. Send 500 transactions from "yield_account" calling "create_yield".
   Each produces a PromiseYield stored in state on shard 0.

3. From "resume_account" (shard 1), send 500 transactions each calling
   promise_yield_resume for one of the data_ids.
   Each produces a PromiseResume receipt forwarded to shard 0.

4. Observe: shard 0's CongestionInfo.delayed_receipts_gas stays near 0
   even as 500 × 200 Tgas = 100 PGas of callback work accumulates in
   the delayed queue.

5. Observe: shard 0 continues accepting new transactions at full rate
   (shard_accepts_transactions() returns Yes) while the delayed queue
   drains at 1 Tgas/chunk, taking ~100,000 chunks to clear.
```

The divergent value is exact: `compute_receipt_congestion_gas` returns `Gas::ZERO` (0 gas units) for every `PromiseResume`, while the actual gas that will be burned when each receipt is processed is the prepaid gas of the matching `PromiseYield` callback — up to `max_total_prepaid_gas` = 300 × 10¹² gas units per receipt. [4](#0-3) [5](#0-4) [6](#0-5)

### Citations

**File:** runtime/runtime/src/congestion_control.rs (L299-322)
```rust
        let size = compute_receipt_size(&receipt)?;
        let gas = compute_receipt_congestion_gas(&receipt, &apply_state.config)?;

        match ReceiptSinkV2::try_forward(
            receipt,
            gas,
            size,
            shard,
            &mut self.sink.outgoing_limit,
            &mut self.sink.outgoing_receipts,
            apply_state,
            &mut self.sink.stats,
        )? {
            ReceiptForwarding::Forwarded => (),
            ReceiptForwarding::NotForwarded(receipt) => {
                self.sink.buffer_receipt(
                    receipt,
                    size,
                    gas,
                    state_update,
                    shard,
                    apply_state.config.use_state_stored_receipt,
                )?;
            }
```

**File:** runtime/runtime/src/congestion_control.rs (L678-714)
```rust
pub(crate) fn compute_receipt_congestion_gas(
    receipt: &Receipt,
    config: &RuntimeConfig,
) -> Result<Gas, IntegerOverflowError> {
    match receipt.versioned_receipt() {
        VersionedReceiptEnum::Action(action_receipt) => {
            // account for gas guaranteed to be used for executing the receipts
            action_receipt_congestion_gas(receipt, config, action_receipt.into())
        }
        VersionedReceiptEnum::Data(_data_receipt) => {
            // Data receipts themselves don't cost gas to execute, their cost is
            // burnt at creation. What we should count, is the gas of the
            // postponed action receipt. But looking that up would require
            // reading the postponed receipt from the trie.
            // Thus, the congestion control MVP does not account for data
            // receipts or postponed receipts.
            Ok(Gas::ZERO)
        }
        VersionedReceiptEnum::PromiseYield(_) => {
            // The congestion control MVP does not account for yielding a
            // promise. Yielded promises are confined to a single account, hence
            // they never cross the shard boundaries. This makes it irrelevant
            // for the congestion MVP, which only counts gas in the outgoing
            // buffers and delayed receipts queue.
            Ok(Gas::ZERO)
        }
        VersionedReceiptEnum::PromiseResume(_) => {
            // The congestion control MVP does not account for resuming a promise.
            // Unlike `PromiseYield`, it is possible that a promise-resume ends
            // up in the delayed receipts queue.
            // But similar to a data receipt, it would be difficult to find the cost
            // of it without expensive state lookups.
            Ok(Gas::ZERO)
        }
        VersionedReceiptEnum::GlobalContractDistribution(_) => Ok(Gas::ZERO),
    }
}
```

**File:** runtime/runtime/src/congestion_control.rs (L838-865)
```rust
    pub(crate) fn push(
        &mut self,
        trie_update: &mut TrieUpdate,
        receipt: &Receipt,
        apply_state: &ApplyState,
    ) -> Result<(), RuntimeError> {
        let config = &apply_state.config;

        let gas = compute_receipt_congestion_gas(&receipt, &config)?;
        let size = compute_receipt_size(&receipt)? as u64;

        // TODO It would be great to have this method take owned Receipt and
        // get rid of the Cow from the Receipt and StateStoredReceipt.
        let receipt = match config.use_state_stored_receipt {
            true => {
                let metadata =
                    StateStoredReceiptMetadata { congestion_gas: gas, congestion_size: size };
                let receipt = StateStoredReceipt::new_borrowed(receipt, metadata);
                ReceiptOrStateStoredReceipt::StateStoredReceipt(receipt)
            }
            false => ReceiptOrStateStoredReceipt::Receipt(Cow::Borrowed(receipt)),
        };

        self.new_delayed_gas = self.new_delayed_gas.checked_add(gas).ok_or(IntegerOverflowError)?;
        self.new_delayed_bytes =
            self.new_delayed_bytes.checked_add(size).ok_or(IntegerOverflowError)?;
        self.queue.push_back(trie_update, &receipt)?;
        Ok(())
```

**File:** runtime/runtime/src/lib.rs (L1421-1490)
```rust
            VersionedReceiptEnum::PromiseResume(data_receipt) => {
                if data_receipt.data.is_none() {
                    // This is a timeout resume. Check the status to see if the receipt has been resumed.
                    let status =
                        get_promise_yield_status(state_update, account_id, data_receipt.data_id)?;
                    if status == Some(PromiseYieldStatus::ResumeInitiated) {
                        // A non-timeout resume receipt has been sent, cancel the timeout.
                        return Ok(None);
                    }
                }

                // Received a new PromiseResume receipt delivering input data for a PromiseYield.
                // It is guaranteed that the PromiseYield has exactly one input data dependency
                // and that it arrives first, so we can simply find and execute it.
                if let Some(yield_receipt) =
                    get_promise_yield_receipt(state_update, account_id, data_receipt.data_id)?
                {
                    // Remove the receipt from the state
                    remove_promise_yield_receipt(state_update, account_id, data_receipt.data_id);

                    // Clear the PromiseYield status
                    remove_promise_yield_status(state_update, account_id, data_receipt.data_id);

                    // Clean up yield_id <-> data_id mappings if this was created by yield_create_with_id
                    if ProtocolFeature::YieldWithId.enabled(apply_state.current_protocol_version) {
                        if let Some(yield_id) = get_yield_id_for_data_id(
                            state_update,
                            account_id,
                            data_receipt.data_id,
                        )? {
                            remove_yield_id_mappings(
                                state_update,
                                account_id,
                                yield_id,
                                data_receipt.data_id,
                            );
                        }
                    }

                    // Save the data into the state keyed by the data_id
                    set_received_data(
                        state_update,
                        account_id.clone(),
                        data_receipt.data_id,
                        &ReceivedData { data: data_receipt.data.clone() },
                    );

                    // Execute the PromiseYield receipt. It will read the input data and clean it
                    // up from the state.
                    return self
                        .apply_action_receipt(
                            state_update,
                            apply_state,
                            pipeline_manager,
                            &yield_receipt,
                            receipt_sink,
                            instant_receipts,
                            validator_proposals,
                            stats,
                            epoch_info_provider,
                            receipt_to_tx,
                        )
                        .map(Some);
                } else {
                    // If the user happens to call `promise_yield_resume` multiple times, it may so
                    // happen that multiple PromiseResume receipts are delivered. We can safely
                    // ignore all but the first.
                    return Ok(None);
                }
            }
```
