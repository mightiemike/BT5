### Title
`validate_tx` Does Not Purge Expired Transactions Before Duplicate-Nonce Check, Causing Valid Transactions to Be Rejected at Gateway Admission — (`crates/apollo_mempool/src/mempool.rs`)

---

### Summary

`Mempool::validate_tx` checks for duplicate nonces against the live pool without first calling `remove_expired_txs()`. `Mempool::add_tx` always calls `remove_expired_txs()` before the same duplicate-nonce check. Because the gateway calls `validate_tx` as a mandatory pre-gate and only calls `add_tx` if `validate_tx` succeeds, an expired transaction that is still sitting in the pool causes `validate_tx` to return `DuplicateNonce` for a valid replacement transaction that `add_tx` would have accepted after cleaning up the expired entry.

---

### Finding Description

`add_tx` opens with an unconditional call to `remove_expired_txs()`:

```rust
// crates/apollo_mempool/src/mempool.rs  line 479-484
pub fn add_tx(&mut self, args: AddTransactionArgs) -> MempoolResult<()> {
    // First remove old transactions from the pool.
    let mut account_nonce_updates = self.remove_expired_txs();
    if !self.is_fifo() {
        self.add_ready_declares();
    }
    ...
    self.add_tx_validations(tx_reference, &args.tx, args.account_state.nonce)?;
```

`validate_tx` performs the same duplicate-nonce check (`validate_fee_escalation`) but **skips** `remove_expired_txs()`:

```rust
// crates/apollo_mempool/src/mempool.rs  line 402-408
pub fn validate_tx(&mut self, args: ValidationArgs) -> MempoolResult<()> {
    let tx_reference = (&args).into();
    self.validate_incoming_tx(tx_reference, args.account_nonce)?;
    self.validate_fee_escalation(tx_reference)?;   // sees expired tx
    Ok(())
}
```

`validate_fee_escalation` with fee-escalation disabled returns `DuplicateNonce` whenever `tx_pool.get_by_address_and_nonce(address, nonce)` is `Some`, regardless of whether that pooled transaction has already exceeded its TTL:

```rust
// crates/apollo_mempool/src/mempool.rs  line 768-773
if !self.config.static_config.enable_fee_escalation {
    if self.tx_pool.get_by_address_and_nonce(address, nonce).is_some() {
        return Err(MempoolError::DuplicateNonce { address, nonce });
    };
    return Ok(None);
}
```

The gateway calls `validate_tx` as a mandatory pre-gate inside `validate_by_mempool` → `run_pre_validation_checks` → `extract_state_nonce_and_run_validations`. If `validate_tx` returns an error, the gateway rejects the transaction and never calls `add_tx`:

```rust
// crates/apollo_gateway/src/stateful_transaction_validator.rs  line 405-406
self.validate_state_preconditions(executable_tx, account_nonce).await?;
validate_by_mempool(executable_tx, account_nonce, mempool_client.clone()).await?;
```

`remove_expired_txs` skips staged (in-flight) transactions but removes all other pool entries older than `transaction_ttl`:

```rust
// crates/apollo_mempool/src/mempool.rs  line 849-852
fn remove_expired_txs(&mut self) -> AddressToNonce {
    let removed_txs = self
        .tx_pool
        .remove_txs_older_than(self.config.dynamic_config.transaction_ttl, &self.state.staged);
```

The test `expired_staged_txs_are_not_deleted` explicitly documents that non-staged expired transactions remain in the pool until `add_tx` or `get_txs` triggers cleanup. In a quiet mempool (no concurrent `add_tx` calls), the window can be arbitrarily long.

---

### Impact Explanation

A user whose transaction at `(address, nonce=N)` has expired but has not yet been evicted (because no `add_tx` has fired since expiry) cannot submit a replacement transaction at the same nonce. The gateway's `validate_tx` pre-gate returns `DuplicateNonce` and the replacement is rejected before sequencing. This matches the allowed impact: **"High. Mempool/gateway/RPC admission accepts invalid transactions or rejects valid transactions before sequencing."**

---

### Likelihood Explanation

The window is bounded by the `transaction_ttl` plus the time until the next unrelated `add_tx` call. In a low-traffic sequencer (e.g., testnet, early mainnet, or a node that is temporarily not receiving new transactions from other senders), the window can span many TTL periods. The condition is reachable by any user who submits a transaction that expires without being committed.

---

### Recommendation

Call `remove_expired_txs()` at the start of `validate_tx`, mirroring `add_tx`:

```rust
pub fn validate_tx(&mut self, args: ValidationArgs) -> MempoolResult<()> {
    self.remove_expired_txs();   // purge stale entries first
    let tx_reference = (&args).into();
    self.validate_incoming_tx(tx_reference, args.account_nonce)?;
    self.validate_fee_escalation(tx_reference)?;
    Ok(())
}
```

---

### Proof of Concept

1. Configure mempool with `transaction_ttl = 60s`, fee-escalation disabled.
2. Submit transaction A at `(address="0x0", nonce=0)` → accepted.
3. Advance clock by 65 seconds (TTL exceeded). Do **not** call `add_tx` for any other address.
4. Submit transaction B at `(address="0x0", nonce=0)` (valid replacement).
5. Gateway calls `validate_tx` → `validate_fee_escalation` → `tx_pool.get_by_address_and_nonce("0x0", 0)` returns expired A → returns `MempoolError::DuplicateNonce`.
6. Gateway rejects B. If instead `add_tx` were called directly (bypassing `validate_tx`), `remove_expired_txs` would evict A first and B would be accepted. [1](#0-0) [2](#0-1) [3](#0-2) [4](#0-3) [5](#0-4) [6](#0-5)

### Citations

**File:** crates/apollo_mempool/src/mempool.rs (L402-408)
```rust
    pub fn validate_tx(&mut self, args: ValidationArgs) -> MempoolResult<()> {
        let tx_reference = (&args).into();
        self.validate_incoming_tx(tx_reference, args.account_nonce)?;
        self.validate_fee_escalation(tx_reference)?;

        Ok(())
    }
```

**File:** crates/apollo_mempool/src/mempool.rs (L479-484)
```rust
    pub fn add_tx(&mut self, args: AddTransactionArgs) -> MempoolResult<()> {
        // First remove old transactions from the pool.
        let mut account_nonce_updates = self.remove_expired_txs();
        if !self.is_fifo() {
            self.add_ready_declares();
        }
```

**File:** crates/apollo_mempool/src/mempool.rs (L768-773)
```rust
        if !self.config.static_config.enable_fee_escalation {
            if self.tx_pool.get_by_address_and_nonce(address, nonce).is_some() {
                return Err(MempoolError::DuplicateNonce { address, nonce });
            };

            return Ok(None);
```

**File:** crates/apollo_mempool/src/mempool.rs (L849-852)
```rust
    fn remove_expired_txs(&mut self) -> AddressToNonce {
        let removed_txs = self
            .tx_pool
            .remove_txs_older_than(self.config.dynamic_config.transaction_ttl, &self.state.staged);
```

**File:** crates/apollo_gateway/src/stateful_transaction_validator.rs (L405-406)
```rust
        self.validate_state_preconditions(executable_tx, account_nonce).await?;
        validate_by_mempool(executable_tx, account_nonce, mempool_client.clone()).await?;
```

**File:** crates/apollo_mempool/src/fee_mempool_test.rs (L1357-1388)
```rust
fn expired_staged_txs_are_not_deleted() {
    // Create a mempool with a fake clock.
    let fake_clock = Arc::new(FakeClock::default());
    let mut mempool = Mempool::new(
        MempoolConfig {
            dynamic_config: MempoolDynamicConfig { transaction_ttl: Duration::from_secs(60) },
            ..Default::default()
        },
        fake_clock.clone(),
    );

    // Add 2 transactions to the mempool, and stage one.
    let staged_tx =
        add_tx_input!(tx_hash: 1, address: "0x0", tx_nonce: 0, account_nonce: 0, tip: 100);
    let nonstaged_tx =
        add_tx_input!(tx_hash: 2, address: "0x0", tx_nonce: 1, account_nonce: 0, tip: 100);
    add_tx(&mut mempool, &staged_tx);
    add_tx(&mut mempool, &nonstaged_tx);
    assert_eq!(mempool.get_txs(1).unwrap(), vec![staged_tx.tx.clone()]);

    // Advance the clock beyond the TTL.
    fake_clock.advance(mempool.config.dynamic_config.transaction_ttl + Duration::from_secs(5));

    // Add another transaction to trigger the cleanup, and verify the staged tx is still in the
    // mempool. The non-staged tx should be removed.
    let another_tx =
        add_tx_input!(tx_hash: 3, address: "0x1", tx_nonce: 0, account_nonce: 0, tip: 100);
    add_tx(&mut mempool, &another_tx);
    let expected_mempool_content =
        MempoolTestContentBuilder::new().with_pool([staged_tx.tx, another_tx.tx]).build();
    expected_mempool_content.assert_eq(&mempool.content());
}
```
