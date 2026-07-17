### Title
`WalletContract` Intermediate Callbacks Silently Discard `caller_deposit` on Early-Return Failure Paths — (`File: runtime/near-wallet-contract/implementation/wallet-contract/src/lib.rs`)

### Summary

The `WalletContract::rlp_execute` entry point correctly captures an external caller's attached NEAR deposit in a `CallerDeposit` struct and passes it through the promise chain so that `rlp_execute_callback` can refund it if the inner cross-contract call fails. However, two intermediate callbacks — `address_check_callback` and `nep_141_storage_balance_callback` — have multiple early-return failure paths that return `PromiseOrValue::Value(ExecuteResponse { … })` directly without ever issuing the refund transfer. In every such path the `caller_deposit` argument is silently dropped, and the attached NEAR tokens remain permanently locked in the wallet contract's balance.

### Finding Description

`CallerDeposit` is constructed in `inner_rlp_execute` from the caller's `env::attached_deposit()` and is threaded through every promise callback so that `rlp_execute_callback` can return the tokens on failure: [1](#0-0) 

`rlp_execute_callback` correctly issues the refund when `PromiseResult::Failed`: [2](#0-1) 

But `address_check_callback` has three early-return paths that never touch `caller_deposit`:

1. **Registrar call failed** (`PromiseResult::Failed`, line 142–148) — returns immediately with an error response, `caller_deposit` is dropped.
2. **Unexpected registrar JSON** (line 151–157) — same early return, `caller_deposit` dropped.
3. **Faulty relayer detected, non-self signer** (line 168–173) — returns `ExecuteResponse` without refunding. [3](#0-2) 

`nep_141_storage_balance_callback` has the same pattern in three paths:

1. **`storage_balance_of` call failed** (`PromiseResult::Failed`, line 204–209) — early return, `caller_deposit` dropped.
2. **Unexpected NEP-141 JSON** (line 213–219) — early return, `caller_deposit` dropped.
3. **Action is not `FunctionCall`** (line 245–253) — early return, `caller_deposit` dropped. [4](#0-3) 

In all six paths the function returns a plain `Value(ExecuteResponse)` — no promise is created to transfer the deposit back — so the tokens are absorbed into the wallet contract's own balance.

### Impact Explanation

Any external account that attaches NEAR tokens to `rlp_execute` and whose execution reaches one of the two multi-step promise paths (EOA base-token transfer requiring a registrar check, or ERC-20 transfer requiring a NEP-141 storage check) will permanently lose their deposit if the intermediate call fails. The registrar or NEP-141 contract can fail for ordinary reasons (network congestion, contract panic, unexpected return value). The lost amount equals the full `attached_deposit` passed by the caller, which can be arbitrarily large. The wallet contract's balance silently grows by that amount with no recovery path.

**Severity: Medium** — funds are lost for the caller; the wallet contract itself is not drained, but any external integrator or relayer that attaches NEAR to `rlp_execute` is at risk.

### Likelihood Explanation

The two affected code paths are exercised whenever:
- A user sends an ETH-emulated base-token transfer to another eth-implicit account (triggering the registrar lookup path), **or**
- A user sends an ERC-20 transfer to an unregistered receiver (triggering the NEP-141 storage-balance path).

Both are documented, intended use cases of the wallet contract. The registrar or NEP-141 contract failing mid-execution is a realistic network condition. No special privilege is required; any external caller can trigger this.

### Recommendation

Each early-return failure branch in `address_check_callback` and `nep_141_storage_balance_callback` must issue the refund before returning, mirroring the logic already present in `rlp_execute_callback`:

```rust
if let Some(CallerDeposit { account_id, yocto_near }) = caller_deposit {
    let refund_promise = env::promise_batch_create(&account_id);
    env::promise_batch_action_transfer(
        refund_promise,
        NearToken::from_yoctonear(yocto_near.into()),
    );
}
return PromiseOrValue::Value(ExecuteResponse { success: false, … });
```

This pattern should be applied to all six early-return sites identified above.

### Proof of Concept

1. Deploy `WalletContract` and the address registrar on a local sandbox.
2. Temporarily make the registrar's `lookup` method panic (or point `ADDRESS_REGISTRAR_ACCOUNT_ID` at a non-existent account).
3. Call `rlp_execute` from an external account with `attached_deposit = 5 NEAR`, passing an ETH-emulated base-token transfer to another eth-implicit account (so the registrar-check path is taken).
4. Observe that `address_check_callback` receives `PromiseResult::Failed` and returns the error response at line 143–147 without issuing any transfer.
5. Check the wallet contract's balance: it has increased by 5 NEAR. The caller's balance has decreased by 5 NEAR (plus gas). No refund receipt is ever produced.

The same experiment applies to `nep_141_storage_balance_callback` by making the token contract's `storage_balance_of` method fail. [5](#0-4) [6](#0-5)

### Citations

**File:** runtime/near-wallet-contract/implementation/wallet-contract/src/types.rs (L172-191)
```rust
/// A data type to keep track of the deposit given by an external caller.
/// This allows us to refund the caller's deposit if the cross-contract call fails.
#[derive(Debug, PartialEq, Eq, Clone, serde::Serialize, serde::Deserialize)]
pub struct CallerDeposit {
    pub account_id: AccountId,
    pub yocto_near: NonZeroU128,
}

impl CallerDeposit {
    pub fn new(context: &ExecutionContext) -> Option<Self> {
        // Only track for external (non-self) callers
        if context.current_account_id == context.predecessor_account_id {
            return None;
        }

        NonZeroU128::new(context.attached_deposit.as_yoctonear()).map(|yocto_near| Self {
            account_id: context.predecessor_account_id.clone(),
            yocto_near,
        })
    }
```

**File:** runtime/near-wallet-contract/implementation/wallet-contract/src/lib.rs (L134-192)
```rust
    pub fn address_check_callback(
        &mut self,
        target: AccountId,
        action: near_action::Action,
        caller_deposit: Option<CallerDeposit>,
    ) -> PromiseOrValue<ExecuteResponse> {
        self.has_in_flight_tx = false;
        let maybe_account_id: Option<AccountId> = match env::promise_result(0) {
            PromiseResult::Failed => {
                return PromiseOrValue::Value(ExecuteResponse {
                    success: false,
                    success_value: None,
                    error: Some("Call to Address Registrar contract failed".into()),
                });
            }
            PromiseResult::Successful(value) => match serde_json::from_slice(&value) {
                Ok(x) => x,
                Err(_) => {
                    return PromiseOrValue::Value(ExecuteResponse {
                        success: false,
                        success_value: None,
                        error: Some("Unexpected response from account registrar".into()),
                    });
                }
            },
        };
        let current_account_id = env::current_account_id();
        let promise = if maybe_account_id.is_some() {
            // We intentionally do not increment the nonce in this case because the
            // error is caused by a faulty relayer, not the user. An honest relayer
            // may still be able to successfully send the user's intended transaction.
            if env::signer_account_id() == current_account_id {
                create_ban_relayer_promise(current_account_id)
            } else {
                return PromiseOrValue::Value(ExecuteResponse {
                    success: false,
                    success_value: None,
                    error: Some("Invalid target: target is address corresponding to existing named account_id".into()),
                });
            }
        } else {
            // We must increment the nonce at this point to prevent replay of the transaction.
            // Recall that the nonce was not incremented in `inner_rlp_execute` in the case that
            // the registrar contract was called (i.e. in the case we end up inside this callback).
            self.nonce = self.nonce.saturating_add(1);
            let ext =
                WalletContract::ext(current_account_id).with_static_gas(RLP_EXECUTE_CALLBACK_GAS);
            match action_to_promise(target, action)
                .map(|p| p.then(ext.rlp_execute_callback(caller_deposit)))
            {
                Ok(p) => p,
                Err(e) => {
                    return PromiseOrValue::Value(e.into());
                }
            }
        };
        self.has_in_flight_tx = true;
        PromiseOrValue::Promise(promise)
    }
```

**File:** runtime/near-wallet-contract/implementation/wallet-contract/src/lib.rs (L194-273)
```rust
    #[private]
    pub fn nep_141_storage_balance_callback(
        &mut self,
        token_id: AccountId,
        receiver_id: AccountId,
        action: near_action::Action,
        caller_deposit: Option<CallerDeposit>,
    ) -> PromiseOrValue<ExecuteResponse> {
        self.has_in_flight_tx = false;
        let maybe_storage_balance: Option<StorageBalance> = match env::promise_result(0) {
            PromiseResult::Failed => {
                return PromiseOrValue::Value(ExecuteResponse {
                    success: false,
                    success_value: None,
                    error: Some(format!("Call to NEP-141 {token_id}::storage_balance_of failed")),
                });
            }
            PromiseResult::Successful(value) => match serde_json::from_slice(&value) {
                Ok(x) => x,
                Err(_) => {
                    return PromiseOrValue::Value(ExecuteResponse {
                        success: false,
                        success_value: None,
                        error: Some("Unexpected response from NEP-141 storage_balance_of".into()),
                    });
                }
            },
        };
        let current_account_id = env::current_account_id();
        let ext = WalletContract::ext(current_account_id).with_static_gas(RLP_EXECUTE_CALLBACK_GAS);
        let promise = match maybe_storage_balance {
            Some(_) => {
                // receiver_id is registered so we can send the transfer
                // without additional actions. Note: in the standard NEP-141
                // implementation it is impossible to have `Some` storage balance,
                // but have it be insufficient to transact.
                match action_to_promise(token_id, action)
                    .map(|p| p.then(ext.rlp_execute_callback(caller_deposit)))
                {
                    Ok(p) => p,
                    Err(e) => {
                        return PromiseOrValue::Value(e.into());
                    }
                }
            }
            None => {
                // receiver_id is not registered so we must call `storage_deposit` first.
                let storage_deposit_args =
                    format!(r#"{{"account_id": "{receiver_id}"}}"#).into_bytes();
                let transfer_function_call = match action {
                    near_action::Action::FunctionCall(x) => x,
                    _ => {
                        return PromiseOrValue::Value(ExecuteResponse {
                            success: false,
                            success_value: None,
                            error: Some(
                                "Expected function call action to perform NEP-141 transfer".into(),
                            ),
                        });
                    }
                };
                Promise::new(token_id)
                    .function_call(
                        "storage_deposit".into(),
                        storage_deposit_args,
                        NEP_141_STORAGE_DEPOSIT_AMOUNT,
                        NEP_141_STORAGE_DEPOSIT_GAS,
                    )
                    .function_call(
                        transfer_function_call.method_name,
                        transfer_function_call.args,
                        transfer_function_call.deposit,
                        transfer_function_call.gas,
                    )
                    .then(ext.rlp_execute_callback(caller_deposit))
            }
        };
        self.has_in_flight_tx = true;
        PromiseOrValue::Promise(promise)
    }
```

**File:** runtime/near-wallet-contract/implementation/wallet-contract/src/lib.rs (L296-305)
```rust
        match env::promise_result(0) {
            PromiseResult::Failed => {
                // The cross-contract call failed, refund the caller if needed
                if let Some(CallerDeposit { account_id, yocto_near }) = caller_deposit {
                    let refund_promise = env::promise_batch_create(&account_id);
                    env::promise_batch_action_transfer(
                        refund_promise,
                        NearToken::from_yoctonear(yocto_near.into()),
                    );
                }
```
