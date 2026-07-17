[1](#0-0) [2](#0-1) [3](#0-2) [4](#0-3) [5](#0-4) [6](#0-5)

### Citations

**File:** runtime/near-vm-runner/src/wasmtime_runner/logic.rs (L2929-2945)
```rust
pub fn promise_batch_action_state_init_by_account_id(
    ctx: &mut Ctx,
    memory: &mut [u8],
    promise_idx: u64,
    account_id_len: u64,
    account_id_ptr: u64,
    amount_ptr: u64,
) -> Result<u64> {
    promise_batch_action_state_init_impl(
        ctx,
        memory,
        promise_idx,
        GlobalContractIdentifierPtrData::AccountId { account_id_len, account_id_ptr },
        amount_ptr,
        "promise_batch_action_state_init_by_account_id",
    )
}
```

**File:** runtime/near-vm-runner/src/wasmtime_runner/logic.rs (L2947-2972)
```rust
fn promise_batch_action_state_init_impl(
    ctx: &mut Ctx,
    memory: &mut [u8],
    promise_idx: u64,
    contract_id_ptr: GlobalContractIdentifierPtrData,
    amount_ptr: u64,
    method_name: &str,
) -> Result<u64> {
    ctx.result_state.gas_counter.pay_base(base)?;
    if ctx.context.is_view() {
        return Err(HostError::ProhibitedInView { method_name: method_name.to_owned() }.into());
    }
    let code = read_contract_id(contract_id_ptr, memory, ctx)?;
    let amount =
        Balance::from_yoctonear(get_u128(&mut ctx.result_state.gas_counter, memory, amount_ptr)?);
    let (receipt_idx, sir) = promise_idx_to_receipt_idx_with_sir(ctx, promise_idx)?;

    pay_action_base(
        &mut ctx.result_state.gas_counter,
        &ctx.fees_config,
        ActionCosts::deterministic_state_init_base,
        sir,
    )?;
    ctx.result_state.deduct_balance(amount)?;
    Ok(ctx.ext.append_action_deterministic_state_init(receipt_idx, code, amount))
}
```
