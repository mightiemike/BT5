### Title
Receipt `max_receipt_size` invariant bypassed: `output_data_receivers` injected into a validated receipt after size check via `promise_return` — (File: `runtime/runtime/src/lib.rs`)

### Summary

The `max_receipt_size` limit (4,194,304 bytes) is enforced on newly-created receipts before the runtime appends `output_data_receivers` from the parent receipt. When a contract uses `promise_return` to redirect a maximum-size receipt, the runtime injects additional `DataReceiver` entries into that receipt **after** the size check, producing an on-chain receipt that exceeds the protocol limit. This is the nearcore analog of M-04: a capacity limit is checked against one component of a composite object, but a second component is appended post-validation, pushing the total over the limit.

### Finding Description

The production call path in `apply_action_receipt()` proceeds as follows:

**Step 1 — Size validation (before mutation):**
`validate_receipt()` in `runtime/runtime/src/verifier.rs` is called with `ValidateReceiptMode::NewReceipt` on each newly-created receipt. This enforces `max_receipt_size`: [1](#0-0) 

**Step 2 — Post-validation mutation:**
After validation, in `apply_action_receipt()`, if the parent receipt has non-empty `output_data_receivers` and the contract returned `ReturnData::ReceiptIndex` (i.e., called `promise_return`), the runtime appends the parent's `output_data_receivers` into the returned receipt's `output_data_receivers` field: [2](#0-1) 

This `extend_from_slice` call happens **after** `validate_receipt` has already passed. Each `DataReceiver` entry (a `CryptoHash` + `AccountId`) adds bytes to the Borsh-serialized receipt, pushing it over `max_receipt_size`.

**Step 3 — Acknowledgment in the codebase:**
The `ValidateReceiptMode::ExistingReceipt` variant, used when processing incoming receipts, explicitly skips the size check and documents the bug: [3](#0-2) 

The `ExistingReceipt` mode is used at the incoming-receipt processing site: [4](#0-3) 

This means oversized receipts are silently accepted into the delayed queue and state.

### Impact Explanation

An unprivileged user who can deploy a contract can produce receipts that exceed `max_receipt_size` and have them accepted into chain state. Consequences:

- The `max_receipt_size` protocol invariant is broken: receipts larger than 4,194,304 bytes enter the chain.
- `ChunkStateWitness` size budgets (designed to stay under ~17 MiB) can be exceeded, since the witness includes incoming receipts.
- All validators must process and store these oversized receipts, which they cannot reject at the `ExistingReceipt` validation site.
- The `outgoing_receipts_big_size_limit` (4.5 MiB) and `outgoing_receipts_usual_size_limit` (100 KiB) cross-shard limits may also be violated if the oversized receipt is routed to another shard. [5](#0-4) 

### Likelihood Explanation

Any account that can deploy a contract can trigger this. The attack requires:
1. Deploying a contract that creates a receipt of size exactly `max_receipt_size - base_overhead` bytes (e.g., via a large `FunctionCall` args payload).
2. Setting up a promise chain `A.then(B)` and calling `promise_return(C)` from within `A`, where `C` is the maximum-size receipt.

The runtime then injects `B`'s `DataReceiver` into `C` after the size check. The test in the codebase demonstrates this is reproducible with a standard contract call: [6](#0-5) 

### Recommendation

Move the `max_receipt_size` check to occur **after** `output_data_receivers` have been injected, or re-validate the receipt size after the `extend_from_slice` mutation at `runtime/runtime/src/lib.rs` lines 1028–1035. Alternatively, account for the maximum possible `output_data_receivers` overhead when validating the initial receipt size.

### Proof of Concept

The exact divergent Borsh bytes: a receipt validated at exactly 4,194,304 bytes has one or more `DataReceiver` structs (each encoding a 32-byte `CryptoHash` + variable-length `AccountId`) appended to its `output_data_receivers` field after validation. The resulting serialized receipt is `4,194,304 + N * sizeof(DataReceiver)` bytes, which exceeds `max_receipt_size`. The codebase's own test confirms the oversized receipt reaches the chain: [3](#0-2) [7](#0-6)

### Citations

**File:** runtime/runtime/src/verifier.rs (L533-541)
```rust
    if mode == ValidateReceiptMode::NewReceipt {
        let receipt_size: u64 =
            borsh::object_length(receipt).unwrap().try_into().expect("Can't convert usize to u64");
        if receipt_size > limit_config.max_receipt_size {
            return Err(ReceiptValidationError::ReceiptSizeExceeded {
                size: receipt_size,
                limit: limit_config.max_receipt_size,
            });
        }
```

**File:** runtime/runtime/src/verifier.rs (L573-586)
```rust
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum ValidateReceiptMode {
    /// Used for validating new receipts that were just created.
    /// More strict than `OldReceipt` mode, which has to handle older receipts.
    NewReceipt,
    /// Used for validating older receipts that were saved in the state/received. Less strict than
    /// NewReceipt validation. Tolerates some receipts that wouldn't pass new validation. It has to
    /// be less strict because:
    /// 1) Older receipts might have been created before new validation rules.
    /// 2) There is a bug which allows to create receipts that are above the size limit. Runtime has
    ///    to handle them gracefully until the receipt size limit bug is fixed.
    ///    See https://github.com/near/nearcore/issues/12606 for details.
    ExistingReceipt,
}
```

**File:** runtime/runtime/src/lib.rs (L1019-1037)
```rust
        if !action_receipt.output_data_receivers().is_empty() {
            if let Ok(ReturnData::ReceiptIndex(receipt_index)) = result.result {
                // Modifying a new receipt instead of sending data
                match result
                    .new_receipts
                    .get_mut(receipt_index as usize)
                    .expect("the receipt for the given receipt index should exist")
                    .receipt_mut()
                {
                    ReceiptEnum::Action(new_action_receipt)
                    | ReceiptEnum::PromiseYield(new_action_receipt) => new_action_receipt
                        .output_data_receivers
                        .extend_from_slice(&action_receipt.output_data_receivers()),
                    ReceiptEnum::ActionV2(new_action_receipt)
                    | ReceiptEnum::PromiseYieldV2(new_action_receipt) => new_action_receipt
                        .output_data_receivers
                        .extend_from_slice(&action_receipt.output_data_receivers()),
                    _ => unreachable!("the receipt should be an action receipt"),
                }
```

**File:** runtime/runtime/src/lib.rs (L2512-2518)
```rust
            validate_receipt(
                &processing_state.apply_state.config.wasm_config.limit_config,
                receipt,
                protocol_version,
                ValidateReceiptMode::ExistingReceipt,
            )
            .map_err(RuntimeError::ReceiptValidationError)?;
```

**File:** runtime/near-test-contracts/test-contract-rs/src/lib.rs (L1910-1939)
```rust
/// Do a promise_return with a large receipt.
/// The receipt has a single FunctionCall action with large args.
/// Creates DAG:
/// C[self.noop(large_args)] -then-> B[self.mark_test_completed()]
#[no_mangle]
pub unsafe fn max_receipt_size_promise_return_method2() {
    input(0);
    let mut args = vec![0u8; register_len(0) as usize];
    read_register(0, args.as_mut_ptr());
    let input_args_json: serde_json::Value = serde_json::from_slice(&args).unwrap();
    let args_size = input_args_json["args_size"].as_u64().unwrap();

    current_account_id(0);
    let current_account = vec![0u8; register_len(0) as usize];
    read_register(0, current_account.as_ptr() as _);

    let large_args = vec![0u8; args_size as usize];
    let noop_method = b"noop";
    let promise_c = promise_create(
        current_account.len() as u64,
        current_account.as_ptr() as u64,
        noop_method.len() as u64,
        noop_method.as_ptr() as u64,
        large_args.len() as u64,
        large_args.as_ptr() as u64,
        0,
        20 * TGAS,
    );

    promise_return(promise_c);
```
