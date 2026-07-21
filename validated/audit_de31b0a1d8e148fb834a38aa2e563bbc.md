### Title
`vm_resources_to_execution_resources` Missing Arms for `range_check96`, `add_mod`, `mul_mod` Cause RPC Simulation/Trace to Reject Valid Transactions — (`crates/apollo_rpc_execution/src/objects.rs`)

---

### Summary

`vm_resources_to_execution_resources` has no match arms for `BuiltinName::range_check96`, `BuiltinName::add_mod`, and `BuiltinName::mul_mod`. All three fall through to the wildcard arm that returns `Err(ExecutionError::UnknownBuiltin)`. Because these builtins are fully costed in every shipped `VersionedConstants` file and the blockifier executes contracts that use them without error, any `starknet_simulateTransactions` or `starknet_traceTransaction` call whose execution trace includes one of these builtins will return an RPC error instead of a valid result.

---

### Finding Description

The match in `vm_resources_to_execution_resources` covers eight builtins explicitly and silently drops `output`: [1](#0-0) 

The three builtins introduced with Cairo's modular-arithmetic extension (`range_check96`, `add_mod`, `mul_mod`) are commented out with a `TODO` and fall to the wildcard: [2](#0-1) 

All three builtins carry non-zero fee costs in every versioned-constants file shipped with the sequencer (e.g. `blockifier_versioned_constants_0_14_2.json`): [3](#0-2) 

The blockifier therefore accepts and executes contracts that use these builtins. The resulting `CallInfo` carries a non-empty `builtin_instance_counter` entry for the unknown builtin, which is passed directly into `vm_resources_to_execution_resources`: [4](#0-3) 

The error propagates through `FunctionInvocation::try_from`, through the trace constructor in `get_trace_constructor`: [5](#0-4) 

and surfaces as an `Err` in `simulate_transactions`: [6](#0-5) 

---

### Impact Explanation

Any user can deploy or call a contract that exercises `range_check96`, `add_mod`, or `mul_mod`. The blockifier will execute the transaction successfully (it is a valid transaction), but `starknet_simulateTransactions` and `starknet_traceTransaction` will return an `ExecutionError::UnknownBuiltin` error to the caller. This matches the allowed High impact: **RPC simulation/tracing rejects valid transactions**.

Fee estimation (`estimate_fee`) does not call `vm_resources_to_execution_resources` and is unaffected. Actual sequencing is also unaffected. Only the simulate/trace RPC surface is broken for these builtins.

---

### Likelihood Explanation

`range_check96` is used by the Cairo compiler for range-check operations in Sierra ≥ 1.x contracts targeting the `all_cairo` layout. Any Sierra contract compiled with a recent compiler version that performs bounded integer arithmetic may emit `range_check96` instances. `add_mod`/`mul_mod` are used by contracts that call the modular-arithmetic builtins directly. All three are present in production VersionedConstants from version 0.13.0 onward.

---

### Recommendation

Add the three missing arms to the match in `vm_resources_to_execution_resources`, mapping them to the appropriate `Builtin` enum variants (or, if the RPC spec does not yet expose them, silently `continue` as is done for `output` until the spec is updated, with a clear comment):

```rust
BuiltinName::range_check96 => {
    builtin_instance_counter.insert(Builtin::RangeCheck96, count)
}
BuiltinName::add_mod => {
    builtin_instance_counter.insert(Builtin::AddMod, count)
}
BuiltinName::mul_mod => {
    builtin_instance_counter.insert(Builtin::MulMod, count)
}
```

If the RPC `Builtin` enum does not yet have these variants, add them and update the serialization layer accordingly.

---

### Proof of Concept

1. Deploy a Sierra contract whose entry point uses `range_check96` (any contract compiled with Sierra ≥ 1.x that performs bounded integer arithmetic will do).
2. Call `starknet_simulateTransactions` with an invoke transaction targeting that entry point.
3. The blockifier executes the transaction successfully; `call_info.resources.vm_resources.builtin_instance_counter` contains `{range_check96: N}` for some `N > 0`.
4. `vm_resources_to_execution_resources` hits the wildcard arm and returns `Err(ExecutionError::UnknownBuiltin { builtin_name: range_check96 })`.
5. `simulate_transactions` propagates the error; the RPC returns an error response instead of a valid `SimulatedTransaction`.

A minimal Rust integration test: construct a `VmExecutionResources` with `builtin_instance_counter = {range_check96: 1}`, call `vm_resources_to_execution_resources`, and assert it returns `Ok(...)` — it currently returns `Err(UnknownBuiltin)`. [7](#0-6)

### Citations

**File:** crates/apollo_rpc_execution/src/objects.rs (L352-356)
```rust
            execution_resources: vm_resources_to_execution_resources(
                call_info.resources.vm_resources,
                gas_vector,
            )?,
        })
```

**File:** crates/apollo_rpc_execution/src/objects.rs (L363-401)
```rust
fn vm_resources_to_execution_resources(
    vm_resources: VmExecutionResources,
    GasVector { l1_gas, l1_data_gas, l2_gas }: GasVector,
) -> ExecutionResult<ExecutionResources> {
    let mut builtin_instance_counter = HashMap::new();
    for (builtin_name, count) in vm_resources.builtin_instance_counter {
        if count == 0 {
            continue;
        }
        let count = u64_from_usize(count);
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
    }
    Ok(ExecutionResources {
        steps: u64_from_usize(vm_resources.n_steps),
        builtin_instance_counter,
        memory_holes: u64_from_usize(vm_resources.n_memory_holes),
        da_gas_consumed: StarknetApiGasVector { l1_gas, l2_gas, l1_data_gas },
        gas_consumed: StarknetApiGasVector::default(),
    })
}
```

**File:** crates/blockifier/resources/blockifier_versioned_constants_0_14_2.json (L69-113)
```json
            "add_mod_builtin": [
                4,
                100
            ],
            "bitwise_builtin": [
                16,
                100
            ],
            "ec_op_builtin": [
                256,
                100
            ],
            "ecdsa_builtin": [
                512,
                100
            ],
            "keccak_builtin": [
                512,
                100
            ],
            "mul_mod_builtin": [
                4,
                100
            ],
            "output_builtin": [
                0,
                1
            ],
            "pedersen_builtin": [
                8,
                100
            ],
            "poseidon_builtin": [
                8,
                100
            ],
            "range_check96_builtin": [
                4,
                100
            ],
            "range_check_builtin": [
                4,
                100
            ]
        }
```

**File:** crates/apollo_rpc_execution/src/execution_utils.rs (L97-123)
```rust
pub fn get_trace_constructor(
    tx: &ExecutableTransactionInput,
) -> fn(TransactionExecutionInfo) -> ExecutionResult<TransactionTrace> {
    match tx {
        ExecutableTransactionInput::Invoke(..) => {
            |execution_info| Ok(TransactionTrace::Invoke(execution_info.try_into()?))
        }
        ExecutableTransactionInput::DeclareV0(..) => {
            |execution_info| Ok(TransactionTrace::Declare(execution_info.try_into()?))
        }
        ExecutableTransactionInput::DeclareV1(..) => {
            |execution_info| Ok(TransactionTrace::Declare(execution_info.try_into()?))
        }
        ExecutableTransactionInput::DeclareV2(..) => {
            |execution_info| Ok(TransactionTrace::Declare(execution_info.try_into()?))
        }
        ExecutableTransactionInput::DeclareV3(..) => {
            |execution_info| Ok(TransactionTrace::Declare(execution_info.try_into()?))
        }
        ExecutableTransactionInput::DeployAccount(..) => {
            |execution_info| Ok(TransactionTrace::DeployAccount(execution_info.try_into()?))
        }
        ExecutableTransactionInput::L1Handler(..) => {
            |execution_info| Ok(TransactionTrace::L1Handler(execution_info.try_into()?))
        }
    }
}
```

**File:** crates/apollo_rpc_execution/src/lib.rs (L1010-1024)
```rust
    execution_results
        .into_iter()
        .zip(trace_constructors)
        .map(|(tx_execution_output, trace_constructor)| {
            let fee_estimation =
                tx_execution_output_to_fee_estimation(&tx_execution_output, &block_context)?;
            match trace_constructor(tx_execution_output.execution_info) {
                Ok(transaction_trace) => Ok(TransactionSimulationOutput {
                    transaction_trace,
                    induced_state_diff: tx_execution_output.induced_state_diff,
                    fee_estimation,
                }),
                Err(e) => Err(e),
            }
        })
```
