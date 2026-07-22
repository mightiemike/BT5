### Title
Unvalidated peer-supplied `transaction_hash` in P2P sync stored and served as authoritative — (File: crates/apollo_p2p_sync/src/client/transaction.rs)

### Summary
`TransactionStreamFactory::parse_data_for_block` receives `FullTransaction` objects from P2P peers and pushes the peer-supplied `transaction_hash` directly into `block_body.transaction_hashes` without verifying it against the hash computed from the transaction content. The unvalidated hash is then committed to the MDBX database and served by the RPC layer as an authoritative value. This is the direct sequencer analog of the external bug: just as `moveVotes()` was missing a check of `s_hasVotedByTokenId[tokenId_]` before acting on a token's state, `parse_data_for_block` is missing a canonicalization check of `transaction_hash` against the transaction content before committing it to storage.

### Finding Description
In `crates/apollo_p2p_sync/src/client/transaction.rs`, the `parse_data_for_block` function processes `FullTransaction` objects received from P2P peers:

```rust
let Some(FullTransaction { transaction, transaction_output, transaction_hash }) =
    maybe_transaction?.0
else { ... };
block_body.transactions.push(transaction);
block_body.transaction_outputs.push(transaction_output);
// TODO(eitan): Validate transaction hash from untrusted sources
block_body.transaction_hashes.push(transaction_hash);   // ← no check
```

The TODO comment explicitly acknowledges that the hash comes from an untrusted source and that validation is missing. The `BlockData::write_to_storage` implementation immediately commits this body to the MDBX database:

```rust
storage_writer.begin_rw_txn()?.append_body(self.1, self.0)?.commit()?;
```

The stored hashes are then used in two downstream paths:

1. **RPC lookups** — `get_transaction_idx_by_hash` and `get_transaction_by_hash` use the stored hash as the lookup key, so a wrong hash causes the correct hash to return "not found" and the wrong hash to return the transaction.
2. **P2P sync server** — `FetchBlockData for FullTransaction` reads the stored hashes back from the DB and re-serves them to other syncing nodes, propagating the wrong hash further.

The canonicalization invariant that must hold is: `stored_hash == tx.calculate_transaction_hash(chain_id, tx.version())`. This invariant is enforced during the gateway ingestion path (`convert_rpc_tx_to_internal` computes and binds the hash) and during the consensus path, but it is absent in the P2P sync client path.

### Impact Explanation
A peer (untrusted by design in P2P sync, as the TODO acknowledges) can supply a `transaction_hash` that does not match the actual transaction content. The wrong hash is committed to storage and served authoritatively:

- `starknet_getTransactionByHash(correct_hash)` returns `TRANSACTION_HASH_NOT_FOUND` for a transaction that is present in the chain.
- `starknet_getTransactionByHash(wrong_hash)` returns the transaction, binding the wrong hash to it in the RPC response.
- Other nodes syncing from this node receive the wrong hash via `FetchBlockData for FullTransaction` and store it in turn.

This matches: **High — RPC execution, fee estimation, tracing, simulation, or pending view returns an authoritative-looking wrong value.**

### Likelihood Explanation
The P2P sync design explicitly treats peers as untrusted (the TODO comment confirms this). Any peer connected to the node can trigger this by responding to a `TransactionQuery` with `FullTransaction` objects carrying an arbitrary `transaction_hash`. No privileged access is required; any node that can establish a P2P connection and respond to sync queries can supply wrong hashes. The missing check is a single-line omission with a known TODO, making it straightforward to exploit.

### Recommendation
In `parse_data_for_block`, after receiving a `FullTransaction`, compute the expected hash from the transaction content and the node's `chain_id`, and reject the peer's data if it does not match the received `transaction_hash`:

```rust
let expected_hash = transaction
    .calculate_transaction_hash(&chain_id, &transaction.version())?;
if expected_hash != transaction_hash {
    return Err(ParseDataError::BadPeer(BadPeerError::InvalidTransactionHash {
        expected: expected_hash,
        received: transaction_hash,
        block_number: block_number.0,
    }));
}
block_body.transaction_hashes.push(transaction_hash);
```

This mirrors the check already present in the gateway path (`tx_without_hash.calculate_transaction_hash(&self.chain_id)?`) and closes the canonicalization gap in the sync path.

### Proof of Concept
1. A peer responds to a `TransactionQuery` for block N with `FullTransaction { transaction: T, transaction_output: O, transaction_hash: WRONG_HASH }` where `WRONG_HASH ≠ T.calculate_transaction_hash(chain_id, version)`.
2. `parse_data_for_block` pushes `WRONG_HASH` into `block_body.transaction_hashes` without verification.
3. `write_to_storage` calls `append_body(N, block_body)`, committing `WRONG_HASH` to the MDBX database.
4. A client calls `starknet_getTransactionByHash(CORRECT_HASH)` → returns `TRANSACTION_HASH_NOT_FOUND`.
5. A client calls `starknet_getTransactionByHash(WRONG_HASH)` → returns transaction `T` with hash `WRONG_HASH`, an authoritative-looking wrong value.
6. A second node syncing from this node receives `WRONG_HASH` via `FetchBlockData for FullTransaction` and stores it, propagating the wrong binding. [1](#0-0) [2](#0-1) [3](#0-2) [4](#0-3)

### Citations

**File:** crates/apollo_p2p_sync/src/client/transaction.rs (L27-43)
```rust
impl BlockData for (BlockBody, BlockNumber) {
    fn write_to_storage<'a>(
        self: Box<Self>,
        storage_writer: &'a mut StorageWriter,
        _class_manager_client: &'a mut SharedClassManagerClient,
    ) -> BoxFuture<'a, Result<(), P2pSyncClientError>> {
        async move {
            let num_txs =
                self.0.transactions.len().try_into().expect("Failed to convert usize to u64");
            storage_writer.begin_rw_txn()?.append_body(self.1, self.0)?.commit()?;
            STATE_SYNC_BODY_MARKER.set_lossy(self.1.unchecked_next().0);
            STATE_SYNC_PROCESSED_TRANSACTIONS.increment(num_txs);
            Ok(())
        }
        .boxed()
    }
}
```

**File:** crates/apollo_p2p_sync/src/client/transaction.rs (L73-90)
```rust
                let Some(FullTransaction { transaction, transaction_output, transaction_hash }) =
                    maybe_transaction?.0
                else {
                    if current_transaction_len == 0 {
                        return Ok(None);
                    } else {
                        return Err(ParseDataError::BadPeer(BadPeerError::NotEnoughTransactions {
                            expected: target_transaction_len,
                            actual: current_transaction_len,
                            block_number: block_number.0,
                        }));
                    }
                };
                block_body.transactions.push(transaction);
                block_body.transaction_outputs.push(transaction_output);
                // TODO(eitan): Validate transaction hash from untrusted sources
                block_body.transaction_hashes.push(transaction_hash);
                current_transaction_len += 1;
```

**File:** crates/apollo_p2p_sync/src/server/mod.rs (L262-293)
```rust
impl FetchBlockData for FullTransaction {
    async fn fetch_block_data(
        block_number: BlockNumber,
        txn: &StorageTxn<'_, db::RO>,
        _class_manager_client: &mut SharedClassManagerClient,
    ) -> Result<Vec<Self>, P2pSyncServerError> {
        let transactions =
            txn.get_block_transactions(block_number)?.ok_or(P2pSyncServerError::BlockNotFound {
                block_hash_or_number: BlockHashOrNumber::Number(block_number),
            })?;
        let transaction_outputs = txn.get_block_transaction_outputs(block_number)?.ok_or(
            P2pSyncServerError::BlockNotFound {
                block_hash_or_number: BlockHashOrNumber::Number(block_number),
            },
        )?;
        let transaction_hashes = txn.get_block_transaction_hashes(block_number)?.ok_or(
            P2pSyncServerError::BlockNotFound {
                block_hash_or_number: BlockHashOrNumber::Number(block_number),
            },
        )?;
        let mut result: Vec<FullTransaction> = Vec::new();
        for (transaction, transaction_output, transaction_hash) in transactions
            .into_iter()
            .zip(transaction_outputs)
            .zip(transaction_hashes)
            .map(|((a, b), c)| (a, b, c))
        {
            result.push(FullTransaction { transaction, transaction_output, transaction_hash });
        }
        Ok(result)
    }
}
```

**File:** crates/apollo_transaction_converter/src/transaction_converter.rs (L388-393)
```rust
                )
            }
        };
        let tx_hash = tx_without_hash.calculate_transaction_hash(&self.chain_id)?;
        Ok((InternalRpcTransaction { tx: tx_without_hash, tx_hash }, proof_data))
    }
```
