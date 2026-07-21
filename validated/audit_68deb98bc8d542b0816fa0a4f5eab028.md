### Title
Gateway Skips Account Signature Verification for Nonce-1 Invoke Transactions via Overly Broad `account_tx_in_pool_or_recent_block` Check - (File: `crates/apollo_gateway/src/stateful_transaction_validator.rs`)

### Summary

The `skip_stateful_validations` function in the Apollo gateway is designed to skip the account's `__validate__` entry point (which performs signature verification) for invoke transactions with nonce=1 when a `deploy_account` is pending. However, the guard condition uses `account_tx_in_pool_or_recent_block`, which returns `true` for **any** transaction from the sender address in the pool — not specifically a `deploy_account`. An unprivileged attacker can exploit this to submit invoke transactions with arbitrary (invalid) signatures that bypass gateway-level signature verification and are admitted to the mempool.

### Finding Description

In `skip_stateful_validations` at [1](#0-0) , the gateway skips the `__validate__` entry point for an invoke transaction when:

1. The transaction type is `Invoke`
2. `tx.nonce() == Nonce(Felt::ONE)`
3. `account_nonce == Nonce(Felt::ZERO)`
4. `account_tx_in_pool_or_recent_block(sender_address)` returns `true`

The intent (per the comment) is to check whether a `deploy_account` transaction exists for the account. However, the actual check delegates to `account_tx_in_pool_or_recent_block`: [2](#0-1) 

This returns `true` if the account has **any** transaction in the pool — including future-nonce invoke transactions (nonce=2, 3, …) that the account owner submitted without first deploying the account. The `max_allowed_nonce_gap` default of 200 means an account with nonce=0 can have nonce=2 invokes in the pool.

When `skip_stateful_validations` returns `true`, `run_validate_entry_point` sets `validate: false` in `ExecutionFlags`: [3](#0-2) 

This means the account contract's `__validate__` entry point — the sole mechanism for signature verification in Starknet — is **not called** during gateway pre-validation. The transaction is admitted to the mempool with an unverified (potentially forged) signature.

Additionally, the `max_nonce_for_validation_skip` field in `StatefulTransactionValidatorConfig` is defined: [4](#0-3) 

but is **never read** inside `skip_stateful_validations`. The function hardcodes the nonce=1 check, making the config field dead code and preventing operators from restricting the skip window.

### Impact Explanation

**High — Mempool/gateway admission accepts invalid transactions before sequencing.**

An attacker can submit an invoke transaction with a forged or empty signature from any account address that has any pending transaction in the mempool (observable from the public mempool), and the gateway will admit it without signature verification. The transaction will subsequently fail during block execution when the batcher calls `__validate__` with `validate: true`, but it has already consumed mempool capacity and batcher execution resources. In a high-throughput environment, this enables a targeted mempool-flooding attack against accounts in the deploy-account flow.

### Likelihood Explanation

**Medium.** The attacker requires no special privileges — only the ability to submit transactions to the public gateway RPC and observe the mempool for accounts with `nonce=0` and a pending transaction. The condition is narrow (nonce=1, account_nonce=0) but the mempool is observable and the `max_allowed_nonce_gap=200` default means many accounts can satisfy the trigger condition.

### Recommendation

1. **Narrow the guard**: Replace `account_tx_in_pool_or_recent_block` with a check that specifically verifies a `deploy_account` transaction (nonce=0, type `DeployAccount`) is present in the pool for the sender address.
2. **Use the config field**: Wire `self.config.max_nonce_for_validation_skip` into `skip_stateful_validations` so the skip window is operator-configurable and bounded, matching the behavior already implemented in `native_blockifier/src/py_validator.rs`. [5](#0-4) 

### Proof of Concept

```
1. Alice submits deploy_account (nonce=0) to the gateway → enters mempool.
   OR Alice submits invoke (nonce=2) to the gateway → enters mempool
      (account_nonce=0, nonce gap=2, within max_allowed_nonce_gap=200 → accepted).

2. Eve observes the mempool: Alice's address has account_nonce=0 and a pending tx.

3. Eve submits:
     type:      INVOKE
     sender:    Alice's address
     nonce:     1
     signature: [] (empty / forged)
     calldata:  <arbitrary>

4. Gateway evaluates skip_stateful_validations:
     tx.nonce() == 1                              ✓
     account_nonce == 0                           ✓
     account_tx_in_pool_or_recent_block(Alice)    ✓  (any tx in pool suffices)
   → skip_validate = true
   → run_validate_entry_point called with validate=false
   → __validate__ NOT called; signature NOT verified
   → Eve's invoke admitted to mempool.

5. Batcher picks up Eve's invoke, executes with validate=true:
   → __validate__ called → fails (invalid signature)
   → transaction rejected, but mempool slot and batcher CPU were consumed.
``` [1](#0-0) [6](#0-5)

### Citations

**File:** crates/apollo_gateway/src/stateful_transaction_validator.rs (L302-312)
```rust
    #[sequencer_latency_histogram(GATEWAY_VALIDATE_TX_LATENCY, true)]
    async fn run_validate_entry_point(
        &mut self,
        executable_tx: &ExecutableTransaction,
        skip_validate: bool,
    ) -> StatefulTransactionValidatorResult<()> {
        let only_query = false;
        let charge_fee = enforce_fee(executable_tx, only_query);
        let strict_nonce_check = false;
        let execution_flags =
            ExecutionFlags { only_query, charge_fee, validate: !skip_validate, strict_nonce_check };
```

**File:** crates/apollo_gateway/src/stateful_transaction_validator.rs (L429-461)
```rust
async fn skip_stateful_validations(
    tx: &ExecutableTransaction,
    account_nonce: Nonce,
    mempool_client: SharedMempoolClient,
) -> StatefulTransactionValidatorResult<bool> {
    if let ExecutableTransaction::Invoke(ExecutableInvokeTransaction { tx, .. }) = tx {
        // check if the transaction nonce is 1, meaning it is post deploy_account, and the
        // account nonce is zero, meaning the account was not deployed yet.
        if tx.nonce() == Nonce(Felt::ONE) && account_nonce == Nonce(Felt::ZERO) {
            let account_address = tx.sender_address();
            debug!("Checking if deploy_account transaction exists for account {account_address}.");
            // We verify that a deploy_account transaction exists for this account. It is sufficient
            // to check if the account exists in the mempool since it means that either it has a
            // deploy_account transaction or transactions with future nonces that passed
            // validations.
            return mempool_client
                .account_tx_in_pool_or_recent_block(tx.sender_address())
                .await
                .map_err(|err| mempool_client_err_to_deprecated_gw_err(&tx.signature(), err))
                .inspect(|exists| {
                    if *exists {
                        debug!("Found deploy_account transaction for account {account_address}.");
                    } else {
                        debug!(
                            "No deploy_account transaction found for account {account_address}."
                        );
                    }
                });
        }
    }

    Ok(false)
}
```

**File:** crates/apollo_mempool/src/mempool.rs (L697-700)
```rust
    pub fn account_tx_in_pool_or_recent_block(&self, account_address: ContractAddress) -> bool {
        self.state.contains_account(account_address)
            || self.tx_pool.contains_account(account_address)
    }
```

**File:** crates/apollo_gateway_config/src/config.rs (L283-283)
```rust
    pub max_nonce_for_validation_skip: Nonce,
```

**File:** crates/native_blockifier/src/py_validator.rs (L113-118)
```rust
        let nonce_small_enough_to_qualify_for_validation_skip =
            tx_nonce <= self.max_nonce_for_validation_skip;

        let skip_validate = deploy_account_not_processed
            && is_post_deploy_nonce
            && nonce_small_enough_to_qualify_for_validation_skip;
```
