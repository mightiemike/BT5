### Title
Gateway Skips Signature Validation for Invoke Transactions When Any Account Transaction Exists in Mempool - (`crates/apollo_gateway/src/stateful_transaction_validator.rs`)

### Summary
The `skip_stateful_validations` function in the gateway skips the `__validate__` entry point (signature verification) for invoke transactions with nonce=1 when the account has **any** transaction in the mempool. An attacker can exploit this by submitting an invoke transaction for a victim account whose deploy_account transaction is pending in the mempool, bypassing signature verification at the gateway admission layer.

### Finding Description
The two-step UX flow for new accounts is:
- **TX1:** User submits `deploy_account` (nonce=0) to the mempool.
- **TX2:** User submits `invoke` (nonce=1) immediately after.

Because the account does not yet exist on-chain when TX2 arrives, the gateway cannot run `__validate__` against it. The `skip_stateful_validations` function was introduced to handle this case:

```rust
// crates/apollo_gateway/src/stateful_transaction_validator.rs:429-461
async fn skip_stateful_validations(
    tx: &ExecutableTransaction,
    account_nonce: Nonce,
    mempool_client: SharedMempoolClient,
) -> StatefulTransactionValidatorResult<bool> {
    if let ExecutableTransaction::Invoke(ExecutableInvokeTransaction { tx, .. }) = tx {
        if tx.nonce() == Nonce(Felt::ONE) && account_nonce == Nonce(Felt::ZERO) {
            // ...
            return mempool_client
                .account_tx_in_pool_or_recent_block(tx.sender_address())
                .await
                // ...
        }
    }
    Ok(false)
}
```

When `skip_validate = true` is returned, `run_validate_entry_point` sets `ExecutionFlags { validate: false }`, which causes `AccountTransaction::validate_tx` to return `Ok(None)` immediately without running the account's `__validate__` entry point — i.e., **no signature check is performed**.

The guard used is `account_tx_in_pool_or_recent_block`, whose implementation is:

```rust
// crates/apollo_mempool/src/mempool.rs:697-700
pub fn account_tx_in_pool_or_recent_block(&self, account_address: ContractAddress) -> bool {
    self.state.contains_account(account_address)
        || self.tx_pool.contains_account(account_address)
}
```

This returns `true` if the account has **any** transaction in the pool — not specifically a `deploy_account` transaction. The inline comment claims this is sufficient because "it means that either it has a deploy_account transaction or transactions with future nonces that passed validations." This reasoning is circular: the `skip_stateful_validations` mechanism itself is what allows nonce=1 invoke transactions to enter the pool without signature validation, so the presence of such a transaction in the pool cannot be used as proof that a legitimate deploy_account exists.

**Attack path:**
1. Victim submits `deploy_account` (nonce=0) to the mempool. The victim's contract address is now visible in the pending pool.
2. Attacker observes the victim's address and submits an `invoke` transaction with `sender_address = victim_address`, `nonce = 1`, and arbitrary calldata.
3. Gateway evaluates: `tx.nonce() == 1`, `account_nonce == 0`, `account_tx_in_pool_or_recent_block(victim_address) == true` (victim's deploy_account is in the pool) → `skip_validate = true`.
4. Gateway calls `run_validate_entry_point` with `validate: false`. No `__validate__` is executed. The attacker's transaction carries no valid signature but is accepted.
5. The attacker's transaction enters the mempool.

At execution time the blockifier runs `__validate__` with `validate: true`, so the attacker's transaction will revert (wrong signature or contract not yet deployed). However, the gateway has admitted an **invalid, unsigned transaction** into the mempool before sequencing.

### Impact Explanation
The gateway accepts an invalid Starknet transaction — one whose signature has not been verified against the account owner — before sequencing. This directly matches:

> **High. Mempool/gateway/RPC admission accepts invalid transactions or rejects valid transactions before sequencing.**

The attacker can inject unsigned invoke transactions for any account that has a pending `deploy_account` in the mempool. While the injected transaction reverts at execution, the admission invariant ("only accept transactions with a valid owner signature") is broken at the gateway boundary.

### Likelihood Explanation
The trigger is unprivileged and requires only:
1. Observing a `deploy_account` transaction in the public mempool (trivially possible via any RPC node).
2. Submitting a crafted `invoke` with `sender_address = victim`, `nonce = 1`, and any calldata.

No special access, keys, or prior relationship with the victim is needed. The window is open for as long as the victim's `deploy_account` remains unconfirmed.

### Recommendation
Replace the coarse `account_tx_in_pool_or_recent_block` check with a check that specifically verifies a `deploy_account` transaction exists for the account in the mempool. Add a new mempool API such as `deploy_account_tx_in_pool(address) -> bool` that inspects the pool for a `DeployAccount` variant at nonce=0 for the given address, and use that in `skip_stateful_validations` instead of the generic account-presence check.

### Proof of Concept

```
// State before attack:
// - victim_address derived from victim's deploy_account tx
// - account_nonce(victim_address) == 0 (not yet deployed)

// Step 1: Victim submits deploy_account (nonce=0) — now in mempool pool
// Step 2: Attacker submits:
InvokeTransaction {
    sender_address: victim_address,
    nonce: 1,
    calldata: <arbitrary>,
    signature: [],   // empty / garbage — never checked
    resource_bounds: <valid>,
    ...
}

// Gateway evaluation in skip_stateful_validations:
//   tx.nonce() == Nonce(ONE)          ✓
//   account_nonce == Nonce(ZERO)      ✓  (victim not deployed yet)
//   account_tx_in_pool_or_recent_block(victim_address) == true  ✓  (victim's deploy_account is in pool)
// → skip_validate = true
// → ExecutionFlags { validate: false }
// → validate_tx returns Ok(None) without running __validate__
// → Transaction admitted to mempool with no signature check
``` [1](#0-0) [2](#0-1) [3](#0-2) [4](#0-3)

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

**File:** crates/blockifier/src/transaction/account_transaction.rs (L999-1001)
```rust
        if !self.execution_flags.validate {
            return Ok(None);
        }
```
