### Title
`max_l2_gas_amount` Upper-Bound Skipped for `Declare` Transactions in Gateway Admission — (`crates/apollo_gateway/src/stateless_transaction_validator.rs`)

---

### Summary

`StatelessTransactionValidator::validate_resource_bounds` explicitly skips the `max_l2_gas_amount` upper-bound check for `Declare` transactions via an empty `if let` branch. Any user can submit a `Declare` transaction with an arbitrarily large `l2_gas.max_amount` (up to `u64::MAX`), bypassing the gateway's admission-control limit and causing the transaction to enter the mempool unchecked.

---

### Finding Description

In `validate_resource_bounds`:

```rust
// TODO(Arni): Consider adding a validation for max_l2_gas_amount for declare.
if let RpcTransaction::Declare(_) = tx {
} else if resource_bounds.l2_gas.max_amount.0 > self.config.max_l2_gas_amount {
    return Err(StatelessTransactionValidatorError::MaxGasAmountTooHigh {
        gas_amount: resource_bounds.l2_gas.max_amount,
        max_gas_amount: self.config.max_l2_gas_amount,
    });
}
```

The `if let RpcTransaction::Declare(_) = tx { }` arm is an empty block. It matches every `Declare` transaction and falls through without executing the `max_l2_gas_amount` check. The `else if` branch is therefore dead for all `Declare` inputs.

The default `max_l2_gas_amount` is `1_210_000_000`. A user can submit a `Declare` with `l2_gas.max_amount = u64::MAX` (≈15,000× the limit) and the gateway will accept it.

The test `valid_l2_gas_amount_on_declare` explicitly documents and asserts this bypass: a `Declare` with `max_amount: GasAmount(200)` passes when `max_l2_gas_amount: 100`.

**Analog mapping to VouchFaucet:**

| VouchFaucet | Sequencer |
|---|---|
| `claimVouch()` has no caller validation | `validate_resource_bounds` has no `max_l2_gas_amount` check for `Declare` |
| Any user can claim unlimited trust | Any user can declare with unlimited `l2_gas.max_amount` |
| `TRUST_AMOUNT` limit is bypassed | `max_l2_gas_amount` limit is bypassed |

The pivot is **RPC/internal conversion**: the `RpcTransaction` → `InternalRpcTransaction` path in `convert_rpc_tx_to_internal` calls `calculate_transaction_hash` using the unchecked `resource_bounds`, so the oversized `max_amount` is hashed into the canonical `tx_hash` and forwarded to the mempool without any downstream re-validation of the gateway's own admission policy.

---

### Impact Explanation

**High — Mempool/gateway/RPC admission accepts invalid transactions before sequencing.**

A `Declare` transaction with `l2_gas.max_amount = u64::MAX` and `max_price_per_unit = min_gas_price`:

1. Passes `validate_resource_bounds` (the bug).
2. Passes all other stateless checks (size, DA mode, Sierra version, compiled-class-hash).
3. Enters the mempool with a canonical `tx_hash` derived from the oversized bounds.
4. When the batcher attempts execution, `verify_can_pay_committed_bounds` computes `max_possible_fee = u64::MAX × max_price_per_unit`, which saturates to `u128::MAX` via `saturating_mul`. The sender cannot hold `u128::MAX` balance, so the blockifier rejects with `ResourcesBoundsExceedBalance`.

The gateway is the designated admission-control boundary. By not enforcing `max_l2_gas_amount` for `Declare`, it accepts transactions its own policy marks invalid, allowing them to occupy mempool slots and force batcher execution attempts that always fail.

---

### Likelihood Explanation

Unprivileged. Any user with a valid Sierra contract can submit a `Declare` transaction. Setting `l2_gas.max_amount` to an arbitrary large value requires no special access. The TODO comment and the dedicated passing test (`valid_l2_gas_amount_on_declare`) confirm the gap is known and currently unmitigated.

---

### Recommendation

Remove the empty `Declare` arm and apply the same `max_l2_gas_amount` check uniformly:

```rust
// Apply to all transaction types, including Declare.
if resource_bounds.l2_gas.max_amount.0 > self.config.max_l2_gas_amount {
    return Err(StatelessTransactionValidatorError::MaxGasAmountTooHigh {
        gas_amount: resource_bounds.l2_gas.max_amount,
        max_gas_amount: self.config.max_l2_gas_amount,
    });
}
```

Update `valid_l2_gas_amount_on_declare` to assert rejection, not acceptance.

---

### Proof of Concept

```
Config: max_l2_gas_amount = 100, min_gas_price = 1

Submit RpcDeclareTransactionV3 {
    resource_bounds: AllResourceBounds {
        l2_gas: ResourceBounds {
            max_amount: GasAmount(18_446_744_073_709_551_615),  // u64::MAX
            max_price_per_unit: GasPrice(1),
        },
        ..Default::default()
    },
    contract_class: <any valid Sierra class>,
    compiled_class_hash: <matching hash>,
    ...
}
```

Gateway path:
1. `validate_resource_bounds` → `if let RpcTransaction::Declare(_) = tx { }` → empty branch taken, check skipped → `Ok(())`.
2. `convert_rpc_tx_to_internal` → `calculate_transaction_hash` hashes `max_amount = u64::MAX` into `tx_hash`.
3. Transaction enters mempool with canonical hash.
4. Batcher picks it up; blockifier rejects with `ResourcesBoundsExceedBalance` (committed fee saturates to `u128::MAX`).

The existing test at lines 173–201 of `stateless_transaction_validator_test.rs` already demonstrates step 1 with `max_amount = 200 >