### Title
Gateway Unconditionally Skips `__validate__` Signature Check for Nonce-1 Invoke Transactions When a Pending Deploy-Account Exists — (`File: crates/apollo_gateway/src/stateful_transaction_validator.rs`)

---

### Summary

`skip_stateful_validations` implements an optional, permanently-on bypass of the account's `__validate__` entry point (the on-chain signature check) for any invoke transaction whose nonce equals `1` and whose sender account has not yet been deployed on-chain, provided any transaction for that address exists in the mempool or a recent block. An unprivileged attacker who observes a victim's pending `deploy_account` transaction can immediately submit a nonce-1 invoke transaction carrying an **arbitrary or empty signature** from the victim's address. The gateway accepts it without running `__validate__`, and the transaction is admitted to the mempool.

---

### Finding Description

`extract_state_nonce_and_run_validations` is the gateway's stateful validation entry point. It calls `run_pre_validation_checks`, which in turn calls `skip_stateful_validations`:

```
extract_state_nonce_and_run_validations
  └─ run_pre_validation_checks
       ├─ validate_state_preconditions   (nonce range, deploy-account uniqueness)
       ├─ validate_by_mempool            (nonce ordering + fee escalation only, NO signature)
       └─ skip_stateful_validations      ← returns true → skip_validate = true
```

`skip_stateful_validations` returns `true` when all three conditions hold:

1. The transaction is an `Invoke`.
2. `tx.nonce() == Nonce(Felt::ONE)`.
3. `account_nonce == Nonce(Felt::ZERO)` (account not yet deployed on-chain).
4. `mempool_client.account_tx_in_pool_or_recent_block(sender)` returns `true`.

When `skip_validate = true`, `run_validate_entry_point` constructs:

```rust
ExecutionFlags { only_query: false, charge_fee, validate: !skip_validate /* = false */, strict_nonce_check: false }
```

and passes it to `blockifier_validator.validate(account_tx)`. With `validate: false`, the blockifier's `StatefulValidator` never calls the account's `__validate__` entry point, so **no signature verification is performed at the gateway**.

`validate_by_mempool` only checks nonce ordering and fee escalation via `ValidationArgs`; it does not inspect the signature field. Therefore the full gateway pipeline accepts the transaction and forwards it to the mempool.

---

### Impact Explanation

An attacker can inject a nonce-1 invoke transaction with a completely invalid (e.g., empty) signature for any account whose `deploy_account` is currently pending in the mempool. The gateway admits it without any cryptographic check. The transaction occupies a mempool slot under the victim's address, potentially:

- **Blocking the victim's own legitimate nonce-1 invoke** (fee-escalation or capacity eviction).
- **Polluting the mempool** with transactions that will fail during block building (the batcher's blockifier *will* run `__validate__` and reject them), wasting batcher execution resources.
- **Disrupting the deploy_account + invoke UX flow** that this feature was designed to improve.

This matches: **High — Mempool/gateway/RPC admission accepts invalid transactions before sequencing.**

---

### Likelihood Explanation

The mempool is public; any observer can watch for `deploy_account` transactions. The attacker needs only to:

1. Observe a pending `deploy_account` for address `A` in the mempool.
2. Craft an invoke transaction with `sender_address = A`, `nonce = 1`, and any signature (including `[]`).
3. Submit it to the gateway before the `deploy_account` is committed.

No privileged access, no special keys, and no on-chain state is required. The window is the entire time the `deploy_account` sits in the mempool (potentially many blocks).

---

### Recommendation

Replace the implicit "account exists in mempool" heuristic with a stricter check that also verifies the signature even when skipping the full `__validate__` entry point, or at minimum restrict the bypass to transactions whose signature passes a lightweight ECDSA pre-check. Alternatively, enforce that `skip_stateful_validations` only returns `true` when the pending mempool entry is specifically a `deploy_account` transaction (not any transaction type), and add a signature-format sanity check (non-empty, correct length) before granting the bypass. The `max_nonce_for_validation_skip` config field already exists in `StatefulTransactionValidatorConfig` but is not wired into `skip_stateful_validations`; using it as an upper bound and adding a signature pre-check would substantially reduce the attack surface.

---

### Proof of Concept

**Setup:** Alice submits a `deploy_account` transaction for address `0xALICE` (nonce=0). It is pending in the mempool; on-chain account nonce is still `0`.

**Attack:**

```
POST /gateway/add_transaction
{
  "type": "INVOKE_FUNCTION",
  "version": "0x3",
  "sender_address": "0xALICE",
  "nonce": "0x1",
  "signature": [],          // ← completely empty / invalid
  "calldata": [...],
  "resource_bounds": { ... valid bounds ... },
  ...
}
```

**Gateway trace:**

1. `get_nonce_from_state(0xALICE)` → `Nonce(0)` (not yet deployed).
2. `validate_by_mempool` → passes (nonce=1 ≤ account_nonce=0 + max_gap=200, no fee escalation conflict).
3. `skip_stateful_validations`: `nonce==1 && account_nonce==0` → calls `account_tx_in_pool_or_recent_block(0xALICE)` → `true` (Alice's deploy_account is in the pool) → returns `true`.
4. `run_validate_entry_point(skip_validate=true)` → `ExecutionFlags { validate: false }` → blockifier skips `__validate__` → **no signature check**.
5. Transaction is forwarded to the mempool and admitted.

**Result:** The attacker's nonce-1 invoke with an empty signature occupies Alice's nonce-1 slot in the mempool. Alice's own legitimate nonce-1 invoke will be rejected or require fee escalation to displace it. [1](#0-0) [2](#0-1) [3](#0-2) [4](#0-3) [5](#0-4)

### Citations

**File:** crates/apollo_gateway/src/stateful_transaction_validator.rs (L158-179)
```rust
    async fn extract_state_nonce_and_run_validations(
        &mut self,
        executable_tx: &ExecutableTransaction,
        mempool_client: SharedMempoolClient,
    ) -> StatefulTransactionValidatorResult<Nonce> {
        let account_nonce =
            self.get_nonce_from_state(executable_tx.contract_address()).await.map_err(|e| {
                // TODO(noamsp): Fix this. Need to map the errors better.
                StarknetError::internal_with_signature_logging(
                    format!(
                        "Failed to get nonce for sender address {}",
                        executable_tx.contract_address()
                    ),
                    &executable_tx.signature(),
                    e,
                )
            })?;
        let skip_validate =
            self.run_pre_validation_checks(executable_tx, account_nonce, mempool_client).await?;
        self.run_validate_entry_point(executable_tx, skip_validate).await?;
        Ok(account_nonce)
    }
```

**File:** crates/apollo_gateway/src/stateful_transaction_validator.rs (L308-312)
```rust
        let only_query = false;
        let charge_fee = enforce_fee(executable_tx, only_query);
        let strict_nonce_check = false;
        let execution_flags =
            ExecutionFlags { only_query, charge_fee, validate: !skip_validate, strict_nonce_check };
```

**File:** crates/apollo_gateway/src/stateful_transaction_validator.rs (L399-410)
```rust
    async fn run_pre_validation_checks(
        &self,
        executable_tx: &ExecutableTransaction,
        account_nonce: Nonce,
        mempool_client: SharedMempoolClient,
    ) -> StatefulTransactionValidatorResult<bool> {
        self.validate_state_preconditions(executable_tx, account_nonce).await?;
        validate_by_mempool(executable_tx, account_nonce, mempool_client.clone()).await?;
        let skip_validate =
            skip_stateful_validations(executable_tx, account_nonce, mempool_client.clone()).await?;
        Ok(skip_validate)
    }
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

**File:** crates/apollo_gateway_config/src/config.rs (L276-300)
```rust
#[derive(Clone, Debug, Serialize, Deserialize, Validate, PartialEq)]
pub struct StatefulTransactionValidatorConfig {
    // If true, ensures the max L2 gas price exceeds (a configurable percentage of) the base gas
    // price of the previous block.
    pub validate_resource_bounds: bool,
    pub max_allowed_nonce_gap: u32,
    pub reject_future_declare_txs: bool,
    pub max_nonce_for_validation_skip: Nonce,
    pub versioned_constants_overrides: Option<VersionedConstantsOverrides>,
    // Minimum gas price as percentage of threshold to accept transactions.
    pub min_gas_price_percentage: u8, // E.g., 80 to require 80% of threshold.
}

impl Default for StatefulTransactionValidatorConfig {
    fn default() -> Self {
        StatefulTransactionValidatorConfig {
            validate_resource_bounds: true,
            max_allowed_nonce_gap: 200,
            reject_future_declare_txs: true,
            max_nonce_for_validation_skip: Nonce(Felt::ONE),
            min_gas_price_percentage: 100,
            versioned_constants_overrides: None,
        }
    }
}
```
