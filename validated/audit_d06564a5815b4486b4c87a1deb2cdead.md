## Analysis

Let me trace the full code path to determine if this is a real compatibility issue.

### Version Selection Gate

The top-level `prepare_contract` in `runtime/near-vm-runner/src/prepare.rs` selects the preparation path based on config flags, not directly on protocol version: [1](#0-0) 

At protocol 83: `reftypes_bulk_memory = false`, `vm_kind = NearVm` → `prepare_v2` is used.
At protocol 84: `reftypes_bulk_memory = true`, `vm_kind = Wasmtime` → `prepare_v3` is used.

This is confirmed by `84.yaml`: [2](#0-1) 

### prepare_v2: No Operand Stack Check

`prepare_v2::prepare_contract` calls `finite_wasm::Analysis` and `.instrument()` directly. There is no `InstrumentContext::new()` call and no `max_operand_stack_bytes_per_function` check anywhere in the function: [3](#0-2) 

### prepare_v3: Operand Stack Check Enforced

`prepare_v3::prepare_contract` passes `config.limit_config.max_operand_stack_bytes_per_function.unwrap_or(u64::MAX)` into `InstrumentContext::new()`: [4](#0-3) 

Inside `InstrumentContext::transform_code_section`, the check is: [5](#0-4) 

### The Limit Value

`84.yaml` introduces `max_operand_stack_bytes_per_function: { new: 8_192 }`: [6](#0-5) 

This is confirmed in the snapshot for protocol 84: [7](#0-6) 

### Deployment and Re-preparation Lifecycle

Contracts are stored as raw WASM bytes in the trie. On each function call, the raw bytes are re-prepared (or loaded from a compiled-contract cache that is **invalidated** on protocol upgrade, as the cache key includes the wasm config). The `action_deploy_contract` path confirms raw bytes are stored: [8](#0-7) 

The cache warming code confirms the cache is invalidated when `wasm_config` changes across a protocol boundary: [9](#0-8) 

---

## Finding

### Title
Protocol v83→v84 Upgrade Permanently Breaks Deployed Contracts Whose Functions Exceed the New 8 192-Byte Operand-Stack Limit — (`runtime/near-vm-runner/src/prepare/prepare_v3.rs`)

### Summary
`prepare_v2` (used at protocol < 84) performs no operand-stack-size check. `prepare_v3` (used at protocol ≥ 84) enforces `max_operand_stack_bytes_per_function = 8 192` via `InstrumentContext::new`. Any contract deployed before the upgrade whose function has a peak operand-stack depth > 8 192 bytes passes preparation at protocol 83 but returns `PrepareError::OperandStackTooLarge` at protocol 84, making it permanently unexecutable.

### Finding Description
The divergence is structural:

| Protocol | Prepare path | Operand-stack check | Result for stack > 8 192 B |
|---|---|---|---|
| ≤ 83 | `prepare_v2` | None | `Ok(instrumented_bytes)` |
| ≥ 84 | `prepare_v3` | `stack_sz > max_operand_stack_bytes_per_function` | `Err(PrepareError::OperandStackTooLarge)` |

The raw WASM bytes are stored unchanged in the trie. After the upgrade the compiled-contract cache is invalidated (cache key includes `wasm_config`), so every subsequent function call re-runs `prepare_contract` under the new config, hitting the new limit.

The concrete trigger: a function that simultaneously holds 1 025 `i64` values on the operand stack reaches 1 025 × 8 = 8 200 bytes, exceeding the 8 192-byte cap. `SimpleMaxStackCfg::size_of_value` returns 8 for `i64`: [10](#0-9) 

### Impact Explanation
Any account whose deployed contract contains such a function loses the ability to call it after the protocol upgrade. The contract code remains in state (the `code_hash` field is unchanged), but every `FunctionCallAction` targeting it will abort with `PrepareError::OperandStackTooLarge`. There is no recovery path short of redeploying a modified contract.

### Likelihood Explanation
Low-to-medium. The 8 192-byte limit (≈ 1 024 simultaneous `i64` operands) is generous for typical hand-written or Rust-compiled contracts. However, generated or heavily unrolled WASM (e.g., from certain compilers or obfuscators) can exceed it. The window is the entire pre-84 deployment history.

### Recommendation
1. **Grandfathering**: Re-run `prepare_contract` under the old config for contracts deployed before protocol 84 and record the result; do not re-validate against new limits for already-accepted contracts.
2. **Alternatively**: Introduce the operand-stack limit in `prepare_v2` as well (with the same value) so the set of accepted contracts is identical across the upgrade boundary.
3. **At minimum**: Document that the protocol 84 upgrade may invalidate contracts with operand-stack depth > 8 192 bytes and provide a migration window.

### Proof of Concept
```rust
// Build a WAT module with a function that pushes 1025 i64 values simultaneously.
let pushes = "(i64.const 0) ".repeat(1025);
let drops  = "(drop) ".repeat(1025);
let wat = format!(r#"(module (func (export "main") {pushes}{drops}))"#);
let wasm = wat::parse_str(&wat).unwrap();

// Protocol 83 config (reftypes_bulk_memory=false, vm_kind=NearVm, no operand-stack limit)
let mut config_83 = /* RuntimeConfigStore::get_config(83) */ ...;
assert!(prepare_contract(&wasm, &config_83, VMKind::NearVm).is_ok());

// Protocol 84 config (reftypes_bulk_memory=true, vm_kind=Wasmtime, limit=8192)
let config_84 = /* RuntimeConfigStore::get_config(84) */ ...;
assert_matches!(
    prepare_contract(&wasm, &config_84, VMKind::Wasmtime),
    Err(PrepareError::OperandStackTooLarge)
);
```

The existing unit test `operand_stack_too_large` in `runtime/near-vm-runner/src/prepare.rs` already demonstrates the boundary behaviour of the limit: [11](#0-10)

### Citations

**File:** runtime/near-vm-runner/src/prepare.rs (L27-33)
```rust
    let features = crate::features::WasmFeatures::new(config);
    if config.reftypes_bulk_memory || config.vm_kind == VMKind::Wasmtime {
        prepare_v3::prepare_contract(original_code, features, config, kind)
    } else {
        prepare_v2::prepare_contract(original_code, features, config, kind)
    }
}
```

**File:** runtime/near-vm-runner/src/prepare.rs (L451-474)
```rust
    /// Reject contracts whose static operand-stack size (bytes) in any single
    /// function exceeds `max_operand_stack_bytes_per_function`.
    #[test]
    fn operand_stack_too_large() {
        with_vm_variants(|kind| {
            // 16 i64 pushes leave 128 bytes on the operand stack at peak.
            // Cap of 127 should reject; cap of 128 should accept.
            let push_then_drop = "(i64.const 0) ".repeat(16) + &"(drop) ".repeat(16);
            let wat = format!(
                r#"(module
                    (func (export "main") {push_then_drop})
                )"#
            );

            let mut config = test_vm_config(Some(kind));
            config.limit_config.max_operand_stack_bytes_per_function = Some(127);
            let r = parse_and_prepare_wat(&config, kind, &wat);
            assert_matches!(r, Err(PrepareError::OperandStackTooLarge));

            config.limit_config.max_operand_stack_bytes_per_function = Some(128);
            let r = parse_and_prepare_wat(&config, kind, &wat);
            assert_matches!(r, Ok(_));
        })
    }
```

**File:** core/parameters/res/runtime_configs/84.yaml (L1-5)
```yaml
reftypes_bulk_memory: { old: false, new: true }
vm_kind: { old: "NearVm", new: "Wasmtime" }
wasm_linear_op_base_cost: { old: 300_000_000_000_000, new: 26_328_192 }
wasm_linear_op_unit_cost: { old: 300_000_000_000_000, new: 822_756 }
max_function_body_size: { new: 196_608 }
```

**File:** core/parameters/res/runtime_configs/84.yaml (L41-41)
```yaml
max_operand_stack_bytes_per_function: { new: 8_192 }
```

**File:** runtime/near-vm-runner/src/prepare/prepare_v2.rs (L383-412)
```rust
pub(crate) fn prepare_contract(
    original_code: &[u8],
    features: crate::features::WasmFeatures,
    config: &Config,
    kind: VMKind,
) -> Result<Vec<u8>, PrepareError> {
    let lightly_steamed = PrepareContext::new(original_code, features, config).run()?;

    let res = finite_wasm::Analysis::new()
        .with_stack(Box::new(SimpleMaxStackCfg))
        .with_gas(Box::new(SimpleGasCostCfg(u64::from(config.regular_op_cost))))
        .analyze(&lightly_steamed)
        .map_err(|err| {
            tracing::error!(?err, ?kind, "analysis failed");
            PrepareError::Deserialization
        })?
        // Make sure contracts can’t call the instrumentation functions via `env`.
        .instrument("internal", &lightly_steamed)
        .map_err(|err| {
            tracing::error!(?err, ?kind, "instrumentation failed");
            PrepareError::Serialization
        })?;
    if let Some(max_size) = config.limit_config.max_instrumented_code_size {
        if res.len() as u64 > max_size {
            tracing::debug!(target: "vm", size=res.len(), ?kind, "instrumented code too large");
            return Err(PrepareError::InstrumentedCodeTooLarge);
        }
    }
    Ok(res)
}
```

**File:** runtime/near-vm-runner/src/prepare/prepare_v3.rs (L417-443)
```rust
    let res = InstrumentContext::new(
        &lightly_steamed,
        "internal",
        &analysis,
        config.regular_op_cost,
        config.limit_config.max_stack_height,
        config.limit_config.max_blocks_per_function.unwrap_or(u64::MAX),
        config.limit_config.max_blocks_per_contract.unwrap_or(u64::MAX),
        config.limit_config.max_params_per_function.unwrap_or(u64::MAX),
        config.limit_config.max_params_per_contract.unwrap_or(u64::MAX),
        config.limit_config.max_operand_stack_bytes_per_function.unwrap_or(u64::MAX),
    )
    .run()
    .map_err(|err| {
        use super::instrument_v3::Error;
        match err {
            Error::TooManyBlocksPerFunction => PrepareError::TooManyBlocksPerFunction,
            Error::TooManyBlocksPerContract => PrepareError::TooManyBlocksPerContract,
            Error::TooManyParamsPerFunction => PrepareError::TooManyParamsPerFunction,
            Error::TooManyParamsPerContract => PrepareError::TooManyParamsPerContract,
            Error::OperandStackTooLarge => PrepareError::OperandStackTooLarge,
            err => {
                tracing::error!(target: "vm", ?err, ?kind, "instrumentation failed");
                PrepareError::Serialization
            }
        }
    })?;
```

**File:** runtime/near-vm-runner/src/prepare/prepare_v3.rs (L461-470)
```rust
    fn size_of_value(&self, ty: wp::ValType) -> u8 {
        use wp::ValType;
        match ty {
            ValType::I32 => 4,
            ValType::I64 => 8,
            ValType::F32 => 4,
            ValType::F64 => 8,
            ValType::V128 => 16,
            ValType::Ref(_) => 8,
        }
```

**File:** runtime/near-vm-runner/src/prepare/instrument_v3.rs (L526-529)
```rust
        let stack_sz = *get_idx!(analysis.function_operand_stack_sizes)?;
        if stack_sz > self.max_operand_stack_bytes_per_function {
            return Err(Error::OperandStackTooLarge);
        }
```

**File:** core/parameters/src/snapshots/near_parameters__config_store__tests__84.json.snap (L254-254)
```text
      "max_operand_stack_bytes_per_function": 8192,
```

**File:** runtime/runtime/src/actions.rs (L285-291)
```rust
    state_update.set_code(account_id.clone(), &code);
    // Precompile the contract under the current `wasm_config`. If a protocol upgrade with a
    // different `wasm_config` is scheduled for the next epoch, also schedule a fire-and-forget
    // warming compile under the new config so the on-disk cache is hot at the boundary.
    // Note: contract compilation costs are already accounted in deploy cost using special logic
    // in estimator (see get_runtime_config() function).
    precompile_contract_with_warming(&code, config, next_config, cache);
```

**File:** chain/chain/src/runtime/mod.rs (L309-320)
```rust
        // Detect an upcoming protocol upgrade that would invalidate the
        // compiled-contract cache, and surface the next epoch's wasm_config.
        let next_wasm_config = self
            .epoch_manager
            .get_next_epoch_protocol_version_from_prev_block(prev_block_hash)
            .ok()
            .filter(|next_pv| *next_pv != current_protocol_version)
            .and_then(|next_pv| {
                let next = Arc::clone(&self.runtime_config_store.get_config(next_pv).wasm_config);
                cache_keys_differ(Arc::clone(&config.wasm_config), Arc::clone(&next))
                    .then_some(next)
            });
```
