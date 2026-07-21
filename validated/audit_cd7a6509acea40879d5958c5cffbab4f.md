### Title
Stale Delayed Declare Queued After Committed-Nonce Retention Window Expires, Bypassing Nonce Guard — (File: crates/apollo_mempool/src/mempool.rs)

### Summary

`add_ready_declares` moves delayed declare transactions from the delay queue to the tx pool by calling `add_tx_inner` directly, bypassing nonce re-validation. The mempool's committed-nonce guard (`MempoolState::committed`) is silently evicted after `committed_nonce_retention_block_count` blocks. When the declare delay elapses after that eviction window, a stale declare (nonce < current account nonce) resolves its account nonce against the stale `incoming_account_nonce` stored at submission time, passes the `tx_reference.nonce == account_nonce` queue-admission check, and is returned by `get_txs` to the batcher as a valid transaction.

### Finding Description

**Root cause — `add_ready_declares` has no nonce guard:**

When a declare is submitted via `add_tx`, it is placed in `delayed_declares` without being inserted into `tx_pool`. After `declare_delay` seconds, `add_ready_declares` pops it and calls `add_tx_inner` directly:

```rust
fn add_ready_declares(&mut self) {
    ...
    let (_submission_time, args) =
        self.delayed_declares.pop_front().expect("Delay declare should exist.");
    self.add_tx_inner(args);   // ← no nonce re-validation
    ...
}
``` [1](#0-0) 

`add_tx_inner` resolves the account nonce via `self.state.resolve_nonce(address, incoming_account_nonce)`:

```rust
fn resolve_nonce(&self, address: ContractAddress, incoming_account_nonce: Nonce) -> Nonce {
    self.staged
        .get(&address)
        .or_else(|| self.committed.get(&address))
        .copied()
        .unwrap_or(incoming_account_nonce)   // ← falls back to stale submission-time nonce
}
``` [2](#0-1) 

**Root cause — committed-nonce eviction:**

`MempoolState::commit` removes committed nonces from `self.committed` once the `CommitHistory` ring-buffer overflows its `committed_nonce_retention_block_count` window:

```rust
let removed_commit = self.commit_history.push(address_to_nonce);
for (address, removed_nonce) in removed_commit {
    let last_committed_nonce = *self.committed.get(&address)...;
    if last_committed_nonce == removed_nonce {
        self.committed.remove(&address);   // ← guard evicted
    }
}
``` [3](#0-2) 

**The analog to `safeApprove`:**

`safeApprove` blocks a second approval when the allowance is non-zero; the fix is `forceApprove`, which does not check existing state. Here, the committed nonce in `MempoolState::committed` is the "non-zero allowance" that blocks stale declares. After `committed_nonce_retention_block_count` blocks the guard is silently zeroed out (evicted), and `add_ready_declares` — which never re-reads on-chain state — then queues the stale declare because `resolve_nonce` falls back to the stale `incoming_account_nonce` stored at submission time.

**Concrete trigger sequence:**

1. Account A submits a declare with nonce 0 → stored in `delayed_declares` with `account_state.nonce = 0`.
2. A competing sequencer commits a transaction for account A at nonce 0; `commit_block` is called with `(A, next_nonce=1)`. The committed nonce `1` is written into `MempoolState::committed`.
3. `remove_up_to_nonce_when_committed` cleans up the `tx_pool` for account A, but the stale declare is still in `delayed_declares` — it is **not** cleaned up.
4. After `committed_nonce_retention_block_count` (default 100) additional blocks, `MempoolState::commit` evicts the committed nonce for A from `self.committed`.
5. The declare delay elapses. `add_ready_declares` calls `add_tx_inner(args)` where `args.account_state.nonce = 0`.
6. `resolve_nonce(A, 0)` finds neither staged nor committed entry → returns `0`.
7. `tx_reference.nonce (0) == account_nonce (0)` → the stale declare is inserted into `tx_queue`.
8. `get_txs` returns it to the batcher as a valid transaction. [4](#0-3) 

The test `stale_delayed_declare_does_not_suppress_gap_detection` explicitly documents "add_ready_declares has no nonce guard" but only tests the case where the committed nonce is still present (1 block committed, retention = 100). It does not cover the post-eviction path. [5](#0-4) 

### Impact Explanation

The mempool admits and queues a transaction whose nonce is strictly less than the on-chain account nonce. The batcher returns it via `get_txs` and includes it in a block proposal. The blockifier's `handle_nonce` rejects it with `TransactionPreValidationError::InvalidNonce` (account nonce 1, incoming nonce 0), causing a reverted transaction in the proposed block. This matches:

> **High. Mempool/gateway/RPC admission accepts invalid transactions or rejects valid transactions before sequencing.**

<cite repo="Thankgoddavid56/sequencer--001

### Citations

**File:** crates/apollo_mempool/src/mempool.rs (L107-113)
```rust
    fn resolve_nonce(&self, address: ContractAddress, incoming_account_nonce: Nonce) -> Nonce {
        self.staged
            .get(&address)
            .or_else(|| self.committed.get(&address))
            .copied()
            .unwrap_or(incoming_account_nonce)
    }
```

**File:** crates/apollo_mempool/src/mempool.rs (L148-157)
```rust
        let removed_commit = self.commit_history.push(address_to_nonce);
        for (address, removed_nonce) in removed_commit {
            let last_committed_nonce = *self
                .committed
                .get(&address)
                .expect("Account in commit history must appear in the committed nonces.");
            if last_committed_nonce == removed_nonce {
                self.committed.remove(&address);
            }
        }
```

**File:** crates/apollo_mempool/src/mempool.rs (L585-617)
```rust
    fn add_tx_inner(&mut self, args: AddTransactionArgs) {
        let AddTransactionArgs { tx, account_state } = args;
        info!("Adding transaction to mempool.");
        trace!("{tx:#?}");

        let tx_reference = TransactionReference::new(&tx);

        // Pre-count this tx as stuck; update_accounts_with_gap will correct the count if this tx
        // resolves the gap.
        if self.accounts_with_gap.contains(&account_state.address) {
            self.n_stuck_txs += 1;
        }

        self.tx_pool
            .insert(tx)
            .expect("Duplicate transactions should cause an error during the validation stage.");

        let AccountState { address, nonce: incoming_account_nonce } = account_state;
        let account_nonce = self.state.resolve_nonce(address, incoming_account_nonce);

        if self.is_fifo() {
            // FIFO mode: add all transactions to the queue immediately, regardless of nonce.
            // Keep all transactions from the same address in the queue.
            self.insert_to_tx_queue(tx_reference);
        } else if tx_reference.nonce == account_nonce {
            // Fee mode: only add transactions with matching account nonce.
            // Remove queued transactions the account might have. This includes old nonce
            // transactions that have become obsolete; those with an equal nonce should
            // already have been removed via fee escalation (`remove_replaced_tx`).
            self.tx_queue.remove_by_address(address);
            self.insert_to_tx_queue(tx_reference);
        }
    }
```

**File:** crates/apollo_mempool/src/mempool.rs (L619-630)
```rust
    fn add_ready_declares(&mut self) {
        let now = self.clock.now();
        while let Some((submission_time, _args)) = self.delayed_declares.front() {
            if now - self.config.static_config.declare_delay < *submission_time {
                break;
            }
            let (_submission_time, args) =
                self.delayed_declares.pop_front().expect("Delay declare should exist.");
            self.add_tx_inner(args);
        }
        self.update_state_metrics();
    }
```

**File:** crates/apollo_mempool/src/fee_mempool_test.rs (L1787-1793)
```rust
    // Once the delay elapses, the stale declare is moved from delayed_declares into tx_pool
    // (add_ready_declares has no nonce guard). It sits there with stale nonce 0 < account nonce
    // 1, so it is never queued. The next commit_block will clean it up via
    // remove_up_to_nonce_when_committed.
    fake_clock.advance(declare_delay);
    mempool.get_txs(1).unwrap();
    assert!(mempool.mempool_snapshot().unwrap().delayed_declares.is_empty());
```
