### Title
Wrong Receiver ID Used in `validate_delegate_action` for `DeterministicStateInit` Inner Actions — (`File: runtime/runtime/src/action_validation.rs`)

### Summary

`validate_delegate_action` passes the **outer transaction's receiver** (`receiver`) to `validate_deterministic_state_init` instead of the **delegate action's own receiver** (`delegate_action.receiver_id()`). This is the direct nearcore analog of the oracle bug: an operation on entity X incorrectly uses the identity of entity Y. The wrong receiver ID is used to check the `DeterministicStateInit` domain invariant (`derived_id == receiver_id`), breaking the invariant in two directions: legitimate meta-transactions that carry a `DeterministicStateInit` inner action always fail tx-level validation, and a crafted meta-transaction whose `state_init` derives to the outer receiver (not the inner delegate receiver) passes tx-level validation when it should not.

### Finding Description

In `validate_delegate_action`, the `inner_receiver` used to validate nested actions is selected as follows: [1](#0-0) 

Before `ProtocolFeature::FixDelegatedDeterministicStateInit` is enabled, the branch falls through to `receiver` — the outer transaction's receiver (the relayer target), not `delegate_action.receiver_id()` (the actual inner action target). This value is then forwarded to `validate_deterministic_state_init`: [2](#0-1) 

The invariant checked there is `derived_id == receiver_id`. With the wrong `receiver_id` supplied, the check compares the derived deterministic account ID against the outer transaction receiver, not the inner delegate receiver. The exact divergent value is the `AccountId` bytes of `outer_tx.receiver` vs. `delegate_action.receiver_id`.

The bug is preserved verbatim in the codebase for backward compatibility with pre-fix protocol versions: [3](#0-2) 

The protocol feature that gates the fix is declared at: [4](#0-3) 

### Impact Explanation

Two concrete effects arise from the wrong receiver being used:

1. **Broken protocol feature (functional):** Any legitimate meta-transaction that wraps a `DeterministicStateInit` inner action fails tx-level validation, because the outer tx receiver (the relayer) is not the deterministic account derived from `state_init`. The feature is entirely unusable via meta-transactions before the fix activates.

2. **Tx-validation bypass (security):** An attacker who controls `state_init_b` (which derives to `det_account_b`) can craft a meta-transaction where `outer_tx.receiver = det_account_b` but `delegate_action.receiver_id = det_account_a`. The old check validates `state_init_b` against `det_account_b` (outer receiver) and passes. The malformed receipt is caught only at the later `validate_receipt` stage, after the transaction has been accepted into the mempool and included in a block. The test confirms this two-stage behavior: [5](#0-4) 

### Likelihood Explanation

The wrong-receiver path is reachable by any unprivileged user who submits a signed meta-transaction (`Action::Delegate` or `Action::DelegateV2`) containing a `DeterministicStateInit` inner action. No validator or privileged role is required. The protocol version gate means the bug is active on any node that has not yet upgraded past `FixDelegatedDeterministicStateInit`.

### Recommendation

The fix is already present: use `delegate_action.receiver_id()` as `inner_receiver` unconditionally (or gated on the protocol feature as currently done). The old `receiver` branch should be treated as dead code once the feature is universally enabled and the backward-compatibility window closes. [6](#0-5) 

### Proof of Concept

The integration test `try_meta_tx_deterministic_receiver_exploit` constructs the exact attack scenario:

- `state_init_b` derives to `det_account_b`
- The outer tx targets `det_account_b` (correct for `state_init_b`)
- The delegate action targets `det_account_a` (wrong account)
- The inner action carries `state_init_b`

Before the fix, the tx passes initial validation (old check: `state_init_b` vs `det_account_b` → match). After the fix, it is rejected at tx validation (new check: `state_init_b` vs `det_account_a` → mismatch → `InvalidDeterministicStateInitReceiver`). [7](#0-6)

### Citations

**File:** runtime/runtime/src/action_validation.rs (L186-208)
```rust
) -> Result<(), ActionsValidationError> {
    let actions = delegate_action.get_actions();
    let inner_receiver =
        if ProtocolFeature::FixDelegatedDeterministicStateInit.enabled(current_protocol_version) {
            // This is the correct receiver id to use for the check.
            delegate_action.receiver_id()
        } else {
            // This is a bug fixed with `FixDelegatedDeterministicStateInit` that
            // validated against the wrong id. This makes it impossible to
            // initialize deterministic accounts from meta transactions.
            // The bug cannot be abused, if someone crafts a state init that passes
            // validation here, it will fail when it is checked as incoming receipt.
            receiver
        };
    validate_actions_with_mode(
        limit_config,
        &actions,
        inner_receiver,
        current_protocol_version,
        mode,
    )?;
    Ok(())
}
```

**File:** runtime/runtime/src/action_validation.rs (L413-427)
```rust
fn validate_deterministic_state_init(
    limit_config: &LimitConfig,
    action: &DeterministicStateInitAction,
    receiver_id: &AccountId,
) -> Result<(), ActionsValidationError> {
    validate_global_contract_identifier(action.state_init.code())?;

    let derived_id = derive_near_deterministic_account_id(&action.state_init);

    if derived_id != *receiver_id {
        return Err(ActionsValidationError::InvalidDeterministicStateInitReceiver {
            derived_id,
            receiver_id: receiver_id.clone(),
        });
    }
```

**File:** core/primitives-core/src/version.rs (L408-410)
```rust
    /// Allow creating `DeterministicStateInitAction` from a delegated action by
    /// fixing the receiver id check.
    FixDelegatedDeterministicStateInit,
```

**File:** test-loop-tests/src/tests/deterministic_account_id.rs (L128-157)
```rust
/// Ensure there is no exploit with invalid deterministic account ids through
/// meta transactions.
///
/// With the old (buggy) code, `validate_delegate_action` used
/// `outer_tx.receiver_id` instead of `delegate_action.receiver_id` when
/// checking inner actions. The exploit tx therefore passes initial tx
/// validation. The exploit is prevented by a following `validate_receipt` check
/// when the meta transaction is unpacked.
#[test]
// Pins to a pre-spice protocol version; skipped under the spice feature.
#[cfg_attr(feature = "protocol_feature_spice", ignore)]
fn test_deterministic_state_init_meta_tx_receiver_check_pre_fix() {
    let fix_version = ProtocolFeature::FixDelegatedDeterministicStateInit.protocol_version();
    let outcome = try_meta_tx_deterministic_receiver_exploit(fix_version - 1)
        .expect("without the fix, exploit tx passes initial tx validation");

    assert_matches!(
        outcome.status,
        FinalExecutionStatus::Failure(TxExecutionError::ActionError(ActionError {
            kind: ActionErrorKind::NewReceiptValidationError(
                ReceiptValidationError::ActionsValidation(
                    ActionsValidationError::InvalidDeterministicStateInitReceiver { .. }
                )
            ),
            ..
        })),
        "expected InvalidDeterministicStateInitReceiver in NewReceiptValidationError, got: {:?}",
        outcome.status
    );
}
```

**File:** test-loop-tests/src/tests/deterministic_account_id.rs (L183-266)
```rust
fn try_meta_tx_deterministic_receiver_exploit(
    protocol_version: ProtocolVersion,
) -> Result<FinalExecutionOutcomeView, InvalidTxError> {
    let mut env = TestEnv::setup_with_version(Balance::from_near(100), protocol_version);
    env.deploy_global_contract(GlobalContractDeployMode::AccountId);

    let (_state_init_a, det_account_a) = env.new_deterministic_account_with_data(small());
    let (state_init_b, det_account_b) = env.new_deterministic_account_with_data(big());
    assert_ne!(det_account_a, det_account_b);

    // Deploy det_account_b and add a full-access key so it can act as meta_tx_sender.
    let user_signer = create_user_test_signer(&env.user_account());
    let storage_balance = env.balance_for_storage(state_init_b.clone());
    let deploy_tx = SignedTransaction::deterministic_state_init(
        env.next_nonce(),
        env.user_account(),
        det_account_b.clone(),
        &user_signer,
        env.get_tx_block_hash(),
        state_init_b.clone(),
        storage_balance,
    );
    env.run_tx(deploy_tx);

    let meta_tx_sender_signer = create_user_test_signer(&det_account_b);
    let pk_base64 = near_primitives_core::serialize::to_base64(
        &borsh::to_vec(&meta_tx_sender_signer.public_key()).unwrap(),
    );
    let add_key_args = serde_json::json!([
        { "batch_create": { "account_id": det_account_b.as_str() }, "id": 0 },
        {
            "action_add_key_with_full_access": {
                "promise_index": 0,
                "public_key": pk_base64,
                "nonce": 0
            },
            "id": 0,
            "return": true
        }
    ]);
    let add_key_tx = SignedTransaction::call(
        env.next_nonce(),
        env.user_account(),
        det_account_b.clone(),
        &user_signer,
        Balance::from_near(2),
        "call_promise".to_owned(),
        serde_json::to_vec(&add_key_args).unwrap(),
        Gas::from_teragas(300),
        env.get_tx_block_hash(),
    );
    env.run_tx(add_key_tx);

    // Craft the exploit: outer_tx.receiver = det_account_b = derive(state_init_b).
    // Old check: det_account_b == derive(state_init_b) passes.
    // The delegate action targets det_account_a, which is the wrong account.
    // In no protocol version can this ever be allowed to be executed successfully.
    let relayer = env.independent_account();
    let relayer_signer = create_user_test_signer(&relayer);
    let inner_action = Action::DeterministicStateInit(Box::new(DeterministicStateInitAction {
        state_init: state_init_b,
        deposit: Balance::ZERO,
    }));
    let delegate_nonce = env.next_nonce_for(&det_account_b);
    let delegate_action = DelegateAction {
        sender_id: det_account_b.clone(),
        receiver_id: det_account_a,
        actions: vec![NonDelegateAction::try_from(inner_action).unwrap()],
        nonce: delegate_nonce,
        max_block_height: 1_000_000,
        public_key: meta_tx_sender_signer.public_key(),
    };
    let signed_delegate_action =
        SignedDelegateAction::sign(&meta_tx_sender_signer, delegate_action);
    let tx = SignedTransaction::from_actions(
        env.next_nonce(),
        relayer,
        det_account_b,
        &relayer_signer,
        vec![Action::Delegate(Box::new(signed_delegate_action))],
        env.get_tx_block_hash(),
    );
    env.try_execute_tx(tx)
}
```
