### Title
`block_casm_hash_v1_declares` Version Gate Silently Bypassed for `DeclareTransaction::V2`, Causing Blockifier/OS Hash Divergence — (`crates/blockifier/src/transaction/transactions.rs`)

---

### Summary

The `block_casm_hash_v1_declares` versioned constant was introduced (in `blockifier_versioned_constants_0_14_1.json`) to enforce that all new declare transactions carry a V2 (Blake2) compiled class hash, matching the OS's `validate_compiled_class_facts` which always uses `blake_compiled_class_hash`. The enforcement call `check_compile_class_hash_v2_declaration()` is gated on `self.version() >= TransactionVersion::THREE`, so `DeclareTransaction::V2` is never checked. A V2 declare carrying a V1 (Poseidon) compiled class hash passes blockifier execution and produces a successful fee estimate or simulation result, while the OS would reject the same transaction because its `validate_compiled_class_facts` computes the Blake2 hash and asserts it equals the hash stored in `contract_class_changes`.

---

### Finding Description

In `DeclareTransaction::run_execute` the V2 and V3 arms share a single match branch, but the hash-version guard is version-restricted:

```rust
// crates/blockifier/src/transaction/transactions.rs  lines 176-189
starknet_api::transaction::DeclareTransaction::V2(DeclareTransactionV2 {
    compiled_class_hash, ..
})
| starknet_api::transaction::DeclareTransaction::V3(DeclareTransactionV3 {
    compiled_class_hash, ..
}) => {
    if context.tx_context.block_context.versioned_constants.block_casm_hash_v1_declares
        && self.version() >= TransactionVersion::THREE   // ← V2 is excluded
    {
        self.check_compile_class_hash_v2_declaration()?
    }
    try_declare(self, state, class_hash, Some(*compiled_class_hash))?
}
``` [1](#0-0) 

`check_compile_class_hash_v2_declaration` computes `casm.hash(&HashVersion::V2)` and rejects any mismatch:

```rust
// crates/starknet_api/src/executable_transaction.rs  lines 228-243
pub fn check_compile_class_hash_v2_declaration(&self) -> Result<(), StarknetApiError> {
    ...
    ContractClass::V1((casm, _)) => casm.hash(&HashVersion::V2),
    ...
    if compiled_class_hash_v2 != compiled_class_hash { ... }
}
``` [2](#0-1) 

The versioned constants diff confirms the flag was activated at 0.14.1:

```
~ /block_casm_hash_v1_declares: true
~ /enable_casm_hash_migration: true
``` [3](#0-2) 

The OS Cairo code always uses `blake_compiled_class_hash` (V2) when validating compiled class facts:

```cairo
// validate_compiled_class_facts
let (hash) = blake_compiled_class_hash(compiled_class, full_contract=FALSE);
assert compiled_class_fact.hash = hash;
``` [4](#0-3) 

The Rust side mirrors this: `ContractClass::compiled_class_hash()` always calls `casm.hash(&HashVersion::V2)`: [5](#0-4) 

The RPC execution layer accepts `DeclareV2` inputs for simulation and estimation: [6](#0-5) 

The `HashVersion` enum and the two distinct hash algorithms are defined here: [7](#0-6) 

---

### Impact Explanation

**High — RPC execution, fee estimation, tracing, or simulation returns an authoritative-looking wrong value.**

A caller submits a `starknet_estimateFee` or `starknet_simulateTransactions` request containing a `DeclareV2` transaction whose `compiled_class_hash` is the Poseidon (V1) hash of the CASM. The blockifier skips `check_compile_class_hash_v2_declaration` (version < THREE), executes the declare successfully, and returns a fee estimate or a `SUCCEEDED` simulation trace. The OS would compute the Blake2 (V2) hash, find a mismatch with the stored V1 hash, and reject the transaction. The RPC response is therefore wrong: it asserts the transaction is valid and provides a fee, while the actual on-chain outcome is rejection.

---

### Likelihood Explanation

**Medium.** The `BROADCASTED_DECLARE_TXN_V2` type is still present in the OpenRPC spec and the RPC simulation path explicitly handles it. Any client that constructs a V2 declare with a Poseidon compiled class hash (e.g., compiled with an older toolchain) and calls `starknet_estimateFee` will receive a misleading success response. The new Apollo gateway rejects non-V3 transactions for sequencing, so the sequencing path is not directly affected, but the RPC simulation divergence is fully reachable without any privilege.

---

### Recommendation

Remove the `self.version() >= TransactionVersion::THREE` guard so that `check_compile_class_hash_v2_declaration` is applied to all Cairo 1 declare transactions (V2 and V3) whenever `block_casm_hash_v1_declares` is true:

```rust
starknet_api::transaction::DeclareTransaction::V2(_)
| starknet_api::transaction::DeclareTransaction::V3(_) => {
    if context.tx_context.block_context.versioned_constants.block_casm_hash_v1_declares {
        self.check_compile_class_hash_v2_declaration()?
    }
    try_declare(self, state, class_hash, Some(*compiled_class_hash))?
}
```

This aligns the blockifier's pre-execution check with the OS's `validate_compiled_class_facts` for all transaction versions that carry a `compiled_class_hash`.

---

### Proof of Concept

1. Compile any Cairo 1 contract with an older toolchain that produces a Poseidon (V1) compiled class hash.
2. Construct a `DeclareV2` transaction with `compiled_class_hash` set to the V1 hash.
3. Call `starknet_estimateFee` or `starknet_simulateTransactions` against a node running with `block_casm_hash_v1_declares = true` (i.e., Starknet version ≥ 0.14.1).
4. Observe that the RPC returns a successful fee estimate / `SUCCEEDED` simulation.
5. Confirm that `check_compile_class_hash_v2_declaration` is never reached for V2 by inspecting the version guard at `crates/blockifier/src/transaction/transactions.rs:184-185`.
6. Confirm that the OS would reject the same transaction by tracing `validate_compiled_class_facts` → `blake_compiled_class_hash` → `assert compiled_class_fact.hash = hash` in `crates/apollo_starknet_os_program/src/cairo/starkware/starknet/core/os/contract_class/compiled_class.cairo:128-131`.

### Citations

**File:** crates/blockifier/src/transaction/transactions.rs (L176-189)
```rust
            starknet_api::transaction::DeclareTransaction::V2(DeclareTransactionV2 {
                compiled_class_hash,
                ..
            })
            | starknet_api::transaction::DeclareTransaction::V3(DeclareTransactionV3 {
                compiled_class_hash,
                ..
            }) => {
                if context.tx_context.block_context.versioned_constants.block_casm_hash_v1_declares
                    && self.version() >= TransactionVersion::THREE
                {
                    self.check_compile_class_hash_v2_declaration()?
                }
                try_declare(self, state, class_hash, Some(*compiled_class_hash))?
```

**File:** crates/starknet_api/src/executable_transaction.rs (L228-243)
```rust
    pub fn check_compile_class_hash_v2_declaration(&self) -> Result<(), StarknetApiError> {
        let compiled_class = &self.class_info.contract_class;
        let compiled_class_hash_v2 = match &compiled_class {
            ContractClass::V0(_) => return Ok(()),
            ContractClass::V1((casm, _)) => casm.hash(&HashVersion::V2),
        };
        let compiled_class_hash = self.compiled_class_hash();
        if compiled_class_hash_v2 != compiled_class_hash {
            let err_var = CasmHashMismatch {
                hash: self.class_hash(),
                actual: compiled_class_hash,
                expected: compiled_class_hash_v2,
            };
            return Err(StarknetApiError::DeclareTransactionCasmHashMissMatch(Box::new(err_var)));
        }
        Ok(())
```

**File:** crates/blockifier/resources/versioned_constants_diff_regression/0.14.0_0.14.1.txt (L1-2)
```text
~ /block_casm_hash_v1_declares: true
~ /enable_casm_hash_migration: true
```

**File:** crates/apollo_starknet_os_program/src/cairo/starkware/starknet/core/os/contract_class/compiled_class.cairo (L128-131)
```text
    let (hash) = blake_compiled_class_hash(compiled_class, full_contract=FALSE);
    %{ LoadClass %}

    assert compiled_class_fact.hash = hash;
```

**File:** crates/starknet_api/src/contract_class/structs.rs (L54-61)
```rust
    pub fn compiled_class_hash(&self) -> CompiledClassHash {
        match self {
            ContractClass::V0(_) => panic!("Cairo 0 doesn't have compiled class hash."),
            ContractClass::V1((casm_contract_class, _sierra_version)) => {
                casm_contract_class.hash(&HashVersion::V2)
            }
        }
    }
```

**File:** crates/apollo_rpc_execution/src/lib.rs (L900-928)
```rust
        ExecutableTransactionInput::DeclareV2(
            declare_tx,
            compiled_class,
            sierra_program_length,
            abi_length,
            only_query,
            sierra_version,
        ) => {
            let class_info = ClassInfo::new(
                &(compiled_class, sierra_version.clone()).into(),
                sierra_program_length,
                abi_length,
                sierra_version,
            )
            .map_err(|err| ExecutionError::BadDeclareTransaction {
                tx: DeclareTransaction::V2(declare_tx.clone()).into(),
                err,
            })?;
            let execution_flags =
                ExecutionFlags { only_query, charge_fee, validate, strict_nonce_check };
            BlockifierTransaction::from_api(
                Transaction::Declare(DeclareTransaction::V2(declare_tx)),
                tx_hash,
                Some(class_info),
                None,
                None,
                execution_flags,
            )
            .map_err(|err| ExecutionError::from((transaction_index, err)))
```

**File:** crates/starknet_api/src/contract_class/compiled_class_hash.rs (L17-23)
```rust
#[derive(Clone, Copy, PartialEq)]
pub enum HashVersion {
    /// Poseidon hash.
    V1,
    /// Blake2Felt252 hash.
    V2,
}
```
