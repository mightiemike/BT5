The evidence is concrete and sufficient to render a verdict. Here is the full analysis:

---

## Key Code Facts

**Prepare path selection** in `prepare.rs`:

```rust
if config.reftypes_bulk_memory || config.vm_kind == VMKind::Wasmtime {
    prepare_v3::prepare_contract(...)
} else {
    prepare_v2::prepare_contract(...)
}
``` [1](#0-0) 

**Protocol version 84 config delta** (`84.yaml`):

```yaml
reftypes_bulk_memory: { old: false, new: true }
vm_kind: { old: "NearVm", new: "Wasmtime" }
max_params_per_function: { new: 64 }   # no "old:" → absent before v84
max_params_per_contract: { new: 50_000 }
``` [2](#0-1) 

At v83: `reftypes_bulk_memory=false`, `vm_kind=NearVm` → condition is `false || false` → **prepare_v2 path**.
At v84: `reftypes_bulk_memory=true`, `vm_kind=Wasmtime` → condition is `true` → **prepare_v3 path**.

**prepare_v2 `PrepareContext` struct** — fields tracked:

```rust
function_limit: u64,
local_limit: u64,
table_limit: u32,
table_element_limit: u32,
type_limit: u64,
```

No `param_limit` field. No per-function parameter count check anywhere in `prepare_v2.rs`. [3](#0-2) 

**prepare_v3 → `InstrumentContext::transform_code_section`** — the check that is absent in v2:

```rust
let num_params: u32 =
    func_type.params().len().try_into().or(Err(Error::TooManyParamsPerFunction))?;
if u64::from(num_params) > self.max_params_per_function {
    return Err(Error::TooManyParamsPerFunction);
}
``` [4](#0-3) 

Called from `prepare_v3::prepare_contract` with `config.limit_config.max_params_per_function.unwrap_or(u64::MAX)` — which at v84 is `Some(64)`: [5](#0-4) 

---

## Verdict

### Title
Wasm contract with >64 parameters per function accepted at protocol v83 (prepare_v2, no param check) becomes permanently unexecutable at protocol v84 (prepare_v3, `max_params_per_function=64`) — (`runtime/near-vm-runner/src/prepare/prepare_v2.rs`)

### Summary
`prepare_v2` (used at v83, where `reftypes_bulk_memory=false` and `vm_kind=NearVm`) performs no per-function parameter count check. `prepare_v3` (used at v84, where both flags flip) enforces `max_params_per_function=64` via `InstrumentContext::transform_code_section`. A contract with a function declaring 65+ parameters is accepted at v83 and permanently rejected at v84.

### Finding Description
The prepare-path selector in `prepare.rs` dispatches to `prepare_v2` when `!reftypes_bulk_memory && vm_kind != Wasmtime`. At protocol version 83 both conditions hold, so `prepare_v2::prepare_contract` is called. `prepare_v2`'s `PrepareContext` tracks `function_limit`, `local_limit`, `table_limit`, `table_element_limit`, and `type_limit`, but has no `param_limit` field and no code that reads `LimitConfig::max_params_per_function`. A wasm module with a function type declaring 65 `i32` parameters passes all v83 checks and is stored on-chain.

At protocol version 84, `84.yaml` simultaneously sets `reftypes_bulk_memory: true`, `vm_kind: Wasmtime`, and introduces `max_params_per_function: 64` (no prior `old:` value, so it was `None` before). Every subsequent call to the deployed contract triggers `prepare_v3::prepare_contract`, which calls `InstrumentContext::new` with `max_params_per_function = 64`, and `transform_code_section` immediately returns `Err(Error::TooManyParamsPerFunction)` for the offending function. The contract cache key changes with the config hash, so the v83-compiled artifact is not reused.

The divergent value is exact:
- **v83**: `prepare_contract(wasm_65_params)` → `Ok(instrumented_bytes)`
- **v84**: `prepare_contract(wasm_65_params)` → `Err(PrepareError::TooManyParamsPerFunction)`

### Impact Explanation
Any contract deployed before the v84 epoch boundary with a function type exceeding 64 parameters becomes permanently unexecutable after the upgrade. Every function call receipt targeting that contract will fail with a `CompilationError(PrepareError::TooManyParamsPerFunction)` outcome. The contract's state is frozen and its funds are inaccessible via normal method calls. This is a one-way, irreversible breakage triggered by a protocol upgrade, not by any action of the contract owner.

### Likelihood Explanation
The wasm specification imposes no limit on function parameter counts. Any developer who wrote a contract with a function taking more than 64 parameters (e.g., a function with many typed arguments, or a generated contract) and deployed it before the v84 epoch would be affected. The breakage is silent at deploy time and only manifests at the first call after the upgrade.

### Recommendation
Before activating `max_params_per_function` in `prepare_v3`, a migration scan should verify that no deployed contract on-chain contains a function type with more than 64 parameters. Alternatively, the limit should be enforced retroactively at deploy time in `prepare_v2` as well (with a matching `old:` value in the yaml), or the limit should be introduced in a separate protocol version prior to the prepare-path switch, so that any violating contract is rejected at deploy time before the path changes.

### Proof of Concept
```rust
// Build a wasm module with one function taking 65 i32 parameters
let params: Vec<ValType> = vec![ValType::I32; 65];
let mut module = Module::new();
let mut types = TypeSection::new();
types.ty().function(params, []);
module.section(&types);
// ... add function/export/code sections ...
let wasm = module.finish();

// At v83 config (reftypes_bulk_memory=false, vm_kind=NearVm, max_params_per_function=None):
let config_v83 = /* protocol 83 RuntimeConfig */;
assert!(prepare_contract(&wasm, &config_v83, VMKind::NearVm).is_ok());

// At v84 config (reftypes_bulk_memory=true, vm_kind=Wasmtime, max_params_per_function=Some(64)):
let config_v84 = /* protocol 84 RuntimeConfig */;
assert_eq!(
    prepare_contract(&wasm, &config_v84, VMKind::Wasmtime),
    Err(PrepareError::TooManyParamsPerFunction)
);
```

### Citations

**File:** runtime/near-vm-runner/src/prepare.rs (L28-32)
```rust
    if config.reftypes_bulk_memory || config.vm_kind == VMKind::Wasmtime {
        prepare_v3::prepare_contract(original_code, features, config, kind)
    } else {
        prepare_v2::prepare_contract(original_code, features, config, kind)
    }
```

**File:** core/parameters/res/runtime_configs/84.yaml (L1-41)
```yaml
reftypes_bulk_memory: { old: false, new: true }
vm_kind: { old: "NearVm", new: "Wasmtime" }
wasm_linear_op_base_cost: { old: 300_000_000_000_000, new: 26_328_192 }
wasm_linear_op_unit_cost: { old: 300_000_000_000_000, new: 822_756 }
max_function_body_size: { new: 196_608 }
max_instrumented_code_size: { new: 16_777_216 }
max_blocks_per_function: { new: 5_000 }
max_blocks_per_contract: { new: 50_000 }
max_types_per_contract: { new: 1024 }
max_deploy_actions_per_receipt: { old: 100, new: 10 }
action_deploy_contract: {
  old: {
    send_sir: 184_765_750_000,
    send_not_sir: 184_765_750_000,
    execution: 184_765_750_000,
  },
  new: {
    send_sir: 184_765_750_000,
    send_not_sir: 184_765_750_000,
    # ~100x compute cost, sets the limit to at most 50 deployments per chunk
    execution: { gas: 184_765_750_000, compute: 20_000_000_000_000},
  }
}
action_deploy_contract_per_byte: {
  old: {
    send_sir: 6_812_999,
    send_not_sir: 47_683_715,
    execution: 64_572_944,
  },
  new: {
    send_sir: 6_812_999,
    send_not_sir: 47_683_715,
    # ~4x compute cost, sets the limit such that a 4MiB contract fills a chunk
    execution: { gas: 64_572_944, compute: 250_000_000},
  }
}
max_params_per_contract: { new: 50_000 }
max_params_per_function: { new: 64 }
deploy_global_contract_execution_base: { old: 0, new: 20_000_000_000_000 }
deploy_global_contract_execution_per_byte: { old: 0, new: 250_000_000 }
max_operand_stack_bytes_per_function: { new: 8_192 }
```

**File:** runtime/near-vm-runner/src/prepare/prepare_v2.rs (L7-21)
```rust
struct PrepareContext<'a> {
    code: &'a [u8],
    config: &'a Config,
    output_code: Vec<u8>,
    function_limit: u64,
    local_limit: u64,
    table_limit: u32,
    table_element_limit: u32,
    type_limit: u64,
    validator: wp::Validator,
    func_validator_allocations: wp::FuncValidatorAllocations,
    before_import_section: bool,
    before_memory_section: bool,
    before_export_section: bool,
}
```

**File:** runtime/near-vm-runner/src/prepare/instrument_v3.rs (L492-496)
```rust
        let num_params: u32 =
            func_type.params().len().try_into().or(Err(Error::TooManyParamsPerFunction))?;
        if u64::from(num_params) > self.max_params_per_function {
            return Err(Error::TooManyParamsPerFunction);
        }
```

**File:** runtime/near-vm-runner/src/prepare/prepare_v3.rs (L417-428)
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
```
