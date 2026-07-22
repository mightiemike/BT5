The code at lines 363–401 is directly visible. Let me also check how this error surfaces in the RPC handler.

### Title
`simulate_transactions` / `trace_transaction` / `trace_block_transactions` fail with `UnknownBuiltin` for any Cairo 1 contract using `range_check96`, `add_mod`, or `mul_mod` builtins — (`crates/apollo_rpc_execution/src/objects.rs`)

### Summary

`vm_resources_to_execution_resources` has an incomplete `match` on `BuiltinName`. The three builtins introduced for Cairo 1 circuits — `range_check96`, `add_mod`, `mul_mod` — are not mapped to their corresponding `Builtin` enum variants. They fall through to the wildcard arm and return `Err(ExecutionError::UnknownBuiltin)`. This error propagates through `FunctionInvocation::try_from` all the way to the RPC layer, causing `starknet_simulateTransactions`, `starknet_traceTransaction`, and `starknet_traceBlockTransactions` to return a hard error for any valid transaction whose execution touches these builtins.

### Finding Description

`vm_resources_to_execution_resources` iterates over `VmExecutionResources::builtin_instance_counter` and maps each `BuiltinName` to a `starknet_api::execution_resources::Builtin`: [1](#0-0) 

The match covers `pedersen`, `range_check`, `ecdsa`, `bitwise`, `ec_op`, `keccak`, `poseidon`, `segment_arena`, and `output`. The three newer builtins are left as a commented-out TODO: [2](#0-1) 

The target type `starknet_api::execution_resources::Builtin` already has `RangeCheck96`, `AddMod`, and `MulMod` variants: [3](#0-2) 

So the mapping is simply missing — not blocked by a version gate or any other guard.

`FunctionInvocation::try_from` calls `vm_resources_to_execution_resources` directly and propagates the error: [4](#0-3) 

`simulate_transactions` in `apollo_rpc_execution/src/lib.rs` calls the trace constructor and propagates any `Err`: [5](#0-4) 

The RPC handler converts this to an RPC error object and returns it to the caller: [6](#0-5) 

### Impact Explanation

Any unprivileged caller invoking `starknet_simulateTransactions`, `starknet_traceTransaction`, or `starknet_traceBlockTransactions` with a transaction that exercises a Cairo 1 contract using `range_check96`, `add_mod`, or `mul_mod` will receive a hard RPC error instead of a valid simulation/trace result. This is a concrete, deterministic wrong value (error vs. success) on the public RPC surface.

This matches the allowed High impact: *"RPC execution, fee estimation, tracing, simulation, or pending view returns an authoritative-looking wrong value."*

### Likelihood Explanation

`range_check96` is used by the standard Cairo 1 range-check-96 builtin, which is emitted by ordinary arithmetic in many contracts. `add_mod`/`mul_mod` are used by Cairo 1 circuit operations. The blockifier already prices and tracks all three: [7](#0-6) 

The `test_rc96_holes` test confirms `range_check96` appears in `VmExecutionResources::builtin_instance_counter` for CASM execution: [8](#0-7) 

The `apollo_batcher` crate already has a complete `From<BuiltinName>` mapping that includes all three: [9](#0-8) 

Any deployed contract using circuits or `range_check96` will trigger this on every simulation/trace call.

### Recommendation

Add the three missing arms to the `match` in `vm_resources_to_execution_resources`:

```rust
BuiltinName::range_check96 => builtin_instance_counter.insert(Builtin::RangeCheck96, count),
BuiltinName::add_mod       => builtin_instance_counter.insert(Builtin::AddMod, count),
BuiltinName::mul_mod       => builtin_instance_counter.insert(Builtin::MulMod, count),
```

This resolves the TODO at lines 385–388 and aligns the conversion with the already-complete `Builtin` enum in `starknet_api`.

### Proof of Concept

1. Deploy a Cairo 1 contract that uses `range_check96` (e.g., calls `u96_guarantee_mul_by_constant`).
2. Call `starknet_simulateTransactions` with an invoke transaction targeting that contract.
3. Blockifier executes the contract; `call_info.resources.vm_resources.builtin_instance_counter` contains `BuiltinName::range_check96 → N` (N > 0).
4. `vm_resources_to_execution_resources` hits the `_` arm and returns `Err(ExecutionError::UnknownBuiltin { builtin_name: range_check96 })`.
5. The RPC handler returns a `TRANSACTION_EXECUTION_ERROR` JSON-RPC error to the caller, even though the transaction is perfectly valid and would be accepted by the sequencer.

### Citations

**File:** crates/apollo_rpc_execution/src/objects.rs (L352-355)
```rust
            execution_resources: vm_resources_to_execution_resources(
                call_info.resources.vm_resources,
                gas_vector,
            )?,
```

**File:** crates/apollo_rpc_execution/src/objects.rs (L373-392)
```rust
        match builtin_name {
            BuiltinName::output => continue,
            BuiltinName::pedersen => builtin_instance_counter.insert(Builtin::Pedersen, count),
            BuiltinName::range_check => builtin_instance_counter.insert(Builtin::RangeCheck, count),
            BuiltinName::ecdsa => builtin_instance_counter.insert(Builtin::Ecdsa, count),
            BuiltinName::bitwise => builtin_instance_counter.insert(Builtin::Bitwise, count),
            BuiltinName::ec_op => builtin_instance_counter.insert(Builtin::EcOp, count),
            BuiltinName::keccak => builtin_instance_counter.insert(Builtin::Keccak, count),
            BuiltinName::poseidon => builtin_instance_counter.insert(Builtin::Poseidon, count),
            BuiltinName::segment_arena => {
                builtin_instance_counter.insert(Builtin::SegmentArena, count)
            }
            // TODO(DanB): what about the following?
            // BuiltinName::range_check96 => todo!(),
            // BuiltinName::add_mod => todo!(),
            // BuiltinName::mul_mod => todo!(),
            _ => {
                return Err(ExecutionError::UnknownBuiltin { builtin_name });
            }
        };
```

**File:** crates/starknet_api/src/execution_resources.rs (L257-263)
```rust
    #[serde(rename = "add_mod_builtin")]
    AddMod,
    #[serde(rename = "mul_mod_builtin")]
    MulMod,
    #[serde(rename = "range_check96_builtin")]
    RangeCheck96,
}
```

**File:** crates/apollo_rpc_execution/src/lib.rs (L1016-1023)
```rust
            match trace_constructor(tx_execution_output.execution_info) {
                Ok(transaction_trace) => Ok(TransactionSimulationOutput {
                    transaction_trace,
                    induced_state_diff: tx_execution_output.induced_state_diff,
                    fee_estimation,
                }),
                Err(e) => Err(e),
            }
```

**File:** crates/apollo_rpc/src/v0_8/api/api_impl.rs (L1119-1121)
```rust
        .await
        .map_err(internal_server_error)?
        .map_err(execution_error_to_error_object_owned)?;
```

**File:** crates/blockifier/src/blockifier_versioned_constants.rs (L1025-1027)
```rust
            BuiltinName::range_check96 => self.range_check96,
            BuiltinName::add_mod => self.add_mod,
            BuiltinName::mul_mod => self.mul_mod,
```

**File:** crates/blockifier/src/transaction/account_transactions_test.rs (L240-244)
```rust
                .total_extended_vm_resources()
                .vm_resources
                .builtin_instance_counter[&BuiltinName::range_check96],
            24
        );
```

**File:** crates/apollo_batcher/src/cende_client_types.rs (L132-135)
```rust
            BuiltinName::add_mod => Builtin::AddMod,
            BuiltinName::mul_mod => Builtin::MulMod,
            BuiltinName::range_check96 => Builtin::RangeCheck96,
        }
```
