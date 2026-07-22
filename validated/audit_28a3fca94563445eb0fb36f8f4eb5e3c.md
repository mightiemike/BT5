Based on my analysis of the full call chain, this is a **valid vulnerability**.

---

### Title
Missing Transaction Hash Validation in P2P Sync Allows Malicious Peer to Corrupt Hash-to-Index Mapping — (`crates/apollo_p2p_sync/src/client/transaction.rs`, `crates/apollo_storage/src/body/mod.rs`)

### Summary

A malicious p2p peer can send a `FullTransaction` with an arbitrary `transaction_hash` field. The p2p sync client accepts it without validation and passes it directly to `append_body`, which stores the attacker-supplied hash as the canonical DB key. Subsequent RPC lookups by the real (computed) transaction hash return `NotFound`, while lookups by the fake hash succeed.

### Finding Description

**Step 1 — P2P sync accepts peer-supplied hash without validation.**

In `parse_data_for_block` (`crates/apollo_p2p_sync/src/client/transaction.rs` lines 73–89), each `FullTransaction` received from a peer is destructured and its `transaction_hash` is pushed directly into `block_body.transaction_hashes` with no check against the computed hash. A developer TODO comment explicitly acknowledges this gap: [1](#0-0) 

```rust
block_body.transactions.push(transaction);
block_body.transaction_outputs.push(transaction_output);
// TODO(eitan): Validate transaction hash from untrusted sources
block_body.transaction_hashes.push(transaction_hash);
```

**Step 2 — `write_to_storage` calls `append_body` with the unvalidated body.** [2](#0-1) 

**Step 3 — `write_transactions` blindly trusts `block_body.transaction_hashes`.**

`write_transactions` zips `transactions`, `transaction_outputs`, and `transaction_hashes` together and stores whatever `tx_hash` appears in the `BlockBody` into both the `transaction_hash_to_idx_table` (the lookup index) and `TransactionMetadata.tx_hash`. There is no call to `calculate_transaction_hash` or any comparison against a computed value: [3](#0-2) 

The comment immediately above the function even flags the missing size/consistency enforcement: [4](#0-3) 

**Contrast with the feeder-gateway path**, which explicitly validates that `transaction.transaction_hash() == receipt.transaction_hash` before constructing the `BlockBody`, and returns a hard error on mismatch: [5](#0-4) 

The p2p path has no equivalent guard.

### Impact Explanation

`get_transaction_idx_by_hash` looks up the `transaction_hash_to_idx_table` keyed by the stored hash: [6](#0-5) 

After a malicious peer injects a fake hash:
- `get_transaction_idx_by_hash(canonical_hash)` → `None`
- `get_transaction_idx_by_hash(fake_hash)` → `Some(index)`

The RPC handler for `starknet_getTransactionByHash` and `starknet_traceTransaction` both call `get_transaction_idx_by_hash` and return `TRANSACTION_HASH_NOT_FOUND` for the canonical hash: [7](#0-6) 

This causes the node to authoritatively deny the existence of a valid, sequenced transaction — fitting the "High. RPC execution… returns an authoritative-looking wrong value" impact category.

### Likelihood Explanation

Any node participating in the p2p network can act as a peer and serve `FullTransaction` messages. No operator or validator privilege is required. The attack is trivially constructable: send a well-formed `Transaction` paired with a random `TransactionHash` felt. The sync client will accept it, store it, and the corruption persists until the block is reverted.

### Recommendation

In `parse_data_for_block` (`transaction.rs`), after receiving each `FullTransaction`, compute the canonical hash using `calculate_transaction_hash` (or `get_transaction_hash`) and compare it against the peer-supplied `transaction_hash`. On mismatch, return `ParseDataError::BadPeer(...)` to disconnect the offending peer. This mirrors the validation already present in the feeder-gateway client path (`block.rs` lines 386–397).

### Proof of Concept

1. Stand up a node syncing via p2p.
2. Act as a peer; respond to a `TransactionQuery` with a `FullTransaction` where `transaction_hash` is a random `Felt` unrelated to the actual transaction.
3. After sync completes, call `get_transaction_idx_by_hash(canonical_hash)` → observe `None`.
4. Call `get_transaction_idx_by_hash(fake_hash)` → observe `Some(TransactionIndex(...))`.
5. Issue `starknet_getTransactionByHash(canonical_hash)` via RPC → observe `TRANSACTION_HASH_NOT_FOUND`.

### Citations

**File:** crates/apollo_p2p_sync/src/client/transaction.rs (L36-36)
```rust
            storage_writer.begin_rw_txn()?.append_body(self.1, self.0)?.commit()?;
```

**File:** crates/apollo_p2p_sync/src/client/transaction.rs (L86-89)
```rust
                block_body.transactions.push(transaction);
                block_body.transaction_outputs.push(transaction_output);
                // TODO(eitan): Validate transaction hash from untrusted sources
                block_body.transaction_hashes.push(transaction_hash);
```

**File:** crates/apollo_storage/src/body/mod.rs (L247-255)
```rust
    fn get_transaction_idx_by_hash(
        &self,
        tx_hash: &TransactionHash,
    ) -> StorageResult<Option<TransactionIndex>> {
        let transaction_hash_to_idx_table =
            self.open_table(&self.tables().transaction_hash_to_idx)?;
        let idx = transaction_hash_to_idx_table.get(self.txn(), tx_hash)?;
        Ok(idx)
    }
```

**File:** crates/apollo_storage/src/body/mod.rs (L597-598)
```rust
// TODO(dvir): consider enforcing that the block_body transactions, transaction_outputs and
// transaction_hashes to be the same size.
```

**File:** crates/apollo_storage/src/body/mod.rs (L622-627)
```rust
        transaction_hash_to_idx_table.insert(txn, tx_hash, &transaction_index)?;
        transaction_metadata_table.append(
            txn,
            &transaction_index,
            &TransactionMetadata { tx_location, tx_output_location, tx_hash: *tx_hash },
        )?;
```

**File:** crates/apollo_starknet_client/src/reader/objects/block.rs (L386-397)
```rust
            // Check that the transaction hash that appears in the receipt is the same as in the
            // transaction.
            if transaction.transaction_hash() != receipt.transaction_hash {
                return Err(ReaderClientError::TransactionReceiptsError(
                    TransactionReceiptsError::MismatchTransactionHash {
                        block_number: header.block_header_without_hash.block_number,
                        tx_index: TransactionOffsetInBlock(i),
                        tx_hash: transaction.transaction_hash(),
                        receipt_tx_hash: receipt.transaction_hash,
                    },
                ));
            }
```

**File:** crates/apollo_rpc/src/v0_8/api/api_impl.rs (L1214-1217)
```rust
            let TransactionIndex(block_number, tx_offset) = storage_txn
                .get_transaction_idx_by_hash(&transaction_hash)
                .map_err(internal_server_error)?
                .ok_or(TRANSACTION_HASH_NOT_FOUND)?;
```
