### Title
Off-by-one in `get_deprecated_transaction_hashes` boundary check accepts deprecated hashes at the exact cutoff block - (`File: crates/starknet_api/src/transaction_hash.rs`)

### Summary

`get_deprecated_transaction_hashes` uses a strict `>` comparison against `MAINNET_TRANSACTION_HASH_WITH_VERSION` (block 1470) instead of `>=`. The comment and constant name both state the intent is "from this block number onwards" — meaning block 1470 itself should be treated as canonical-only. Because of the off-by-one, block 1470 falls into the `else` branch and deprecated hashes are computed and returned, making `validate_transaction_hash` accept a deprecated (wrong-algorithm) hash for any transaction at exactly block 1470 on mainnet.

### Finding Description

In `crates/starknet_api/src/transaction_hash.rs`:

```rust
// On mainnet, from this block number onwards, there are no deprecated transactions,
// enabling us to validate against a single hash calculation.
pub const MAINNET_TRANSACTION_HASH_WITH_VERSION: BlockNumber = BlockNumber(1470);

fn get_deprecated_transaction_hashes(
    chain_id: &ChainId,
    block_number: &BlockNumber,
    ...
) -> Result<Vec<TransactionHash>, StarknetApiError> {
    Ok(if chain_id == &ChainId::Mainnet && block_number > &MAINNET_TRANSACTION_HASH_WITH_VERSION {
        vec![]   // ← block 1470 does NOT enter here
    } else {
        // deprecated hashes are computed ← block 1470 enters here
        match transaction { ... }
    })
}
``` [1](#0-0) 

The condition `block_number > &MAINNET_TRANSACTION_HASH_WITH_VERSION` evaluates to `false` when `block_number == 1470`, so the deprecated hash list is populated for that block. `validate_transaction_hash` then calls:

```rust
possible_hashes.push(get_transaction_hash(...)?);   // canonical hash
Ok(possible_hashes.contains(&expected_hash))        // accepts either
``` [2](#0-1) 

For `InvokeTransaction::V0` and `Deploy` transactions at block 1470, the deprecated hash omits the `version` and `max_fee` fields from the Pedersen chain. A transaction whose `expected_hash` was computed under the deprecated algorithm will pass validation at block 1470 even though the canonical algorithm should be the only accepted one.

### Impact Explanation

`validate_transaction_hash` is the authoritative hash-validation gate used in the sync and RPC re-execution paths. Accepting a deprecated hash at block 1470 means:

- A syncing node will store a transaction whose recorded hash was computed under the wrong algorithm.
- Any subsequent RPC call (`starknet_getTransactionByHash`, `starknet_traceTransaction`, fee estimation) that re-derives or re-validates the hash will operate on a hash that does not match the canonical Starknet hash for that transaction.
- This constitutes an authoritative-looking wrong value returned from RPC execution/tracing/simulation, matching the **High** impact scope: *"RPC execution, fee estimation, tracing, simulation, or pending view returns an authoritative-looking wrong value."*

### Likelihood Explanation

The boundary is a single specific block (1470) on mainnet. The deprecated hash path is only reachable for `InvokeTransaction::V0` and `Deploy` transaction types. A peer or RPC caller that supplies a block-1470 transaction with a deprecated hash triggers the incorrect acceptance path without any privileged access. The trigger is deterministic and requires no brute-force.

### Recommendation

Change the strict greater-than to greater-than-or-equal:

```diff
- Ok(if chain_id == &ChainId::Mainnet && block_number > &MAINNET_TRANSACTION_HASH_WITH_VERSION {
+ Ok(if chain_id == &ChainId::Mainnet && block_number >= &MAINNET_TRANSACTION_HASH_WITH_VERSION {
``` [3](#0-2) 

This aligns the code with the comment ("from this block number onwards") and the constant name (`MAINNET_TRANSACTION_HASH_WITH_VERSION`), ensuring block 1470 itself returns an empty deprecated-hash list and is validated only against the canonical hash.

### Proof of Concept

1. Construct an `InvokeTransaction::V0` whose canonical hash (with version + max_fee) is `H_canonical`.
2. Compute its deprecated hash (without version, without max_fee) to obtain `H_deprecated ≠ H_canonical`.
3. Call `validate_transaction_hash(&tx, &BlockNumber(1470), &ChainId::Mainnet, H_deprecated, &opts)`.
4. With the current `>` condition, `get_deprecated_transaction_hashes` returns `[H_deprecated]` for block 1470; `possible_hashes` becomes `[H_deprecated, H_canonical]`; `possible_hashes.contains(&H_deprecated)` returns `true`.
5. The function incorrectly reports the transaction as valid under `H_deprecated` at block 1470, even though the canonical algorithm should be the sole accepted hash from that block onward.

### Citations

**File:** crates/starknet_api/src/transaction_hash.rs (L125-163)
```rust
// On mainnet, from this block number onwards, there are no deprecated transactions,
// enabling us to validate against a single hash calculation.
pub const MAINNET_TRANSACTION_HASH_WITH_VERSION: BlockNumber = BlockNumber(1470);

// Calculates a list of deprecated hashes for a transaction.
fn get_deprecated_transaction_hashes(
    chain_id: &ChainId,
    block_number: &BlockNumber,
    transaction: &Transaction,
    transaction_options: &TransactionOptions,
) -> Result<Vec<TransactionHash>, StarknetApiError> {
    let transaction_version = &signed_tx_version_from_tx(transaction, transaction_options);
    Ok(if chain_id == &ChainId::Mainnet && block_number > &MAINNET_TRANSACTION_HASH_WITH_VERSION {
        vec![]
    } else {
        match transaction {
            Transaction::Declare(_) => vec![],
            Transaction::Deploy(deploy) => {
                vec![get_deprecated_deploy_transaction_hash(deploy, chain_id, transaction_version)?]
            }
            Transaction::DeployAccount(_) => vec![],
            Transaction::Invoke(invoke) => match invoke {
                InvokeTransaction::V0(invoke_v0) => {
                    vec![get_deprecated_invoke_transaction_v0_hash(
                        invoke_v0,
                        chain_id,
                        transaction_version,
                    )?]
                }
                InvokeTransaction::V1(_) | InvokeTransaction::V3(_) => vec![],
            },
            Transaction::L1Handler(l1_handler) => get_deprecated_l1_handler_transaction_hashes(
                l1_handler,
                chain_id,
                transaction_version,
            )?,
        }
    })
}
```

**File:** crates/starknet_api/src/transaction_hash.rs (L170-185)
```rust
pub fn validate_transaction_hash(
    transaction: &Transaction,
    block_number: &BlockNumber,
    chain_id: &ChainId,
    expected_hash: TransactionHash,
    transaction_options: &TransactionOptions,
) -> Result<bool, StarknetApiError> {
    let mut possible_hashes = get_deprecated_transaction_hashes(
        chain_id,
        block_number,
        transaction,
        transaction_options,
    )?;
    possible_hashes.push(get_transaction_hash(transaction, chain_id, transaction_options)?);
    Ok(possible_hashes.contains(&expected_hash))
}
```
