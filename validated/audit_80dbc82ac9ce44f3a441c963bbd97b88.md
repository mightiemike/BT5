### Title
`block_casm_hash_v1_declares` Version Gate Skips `DeclareTransaction::V2`, Allowing V1 CASM Hash to Be Committed to State — (`File: crates/blockifier/src/transaction/transactions.rs`)

### Summary

The `block_casm_hash_v1_declares` flag in `VersionedConstants` (enabled from Starknet v0.14.1 onward) is intended to reject any new declaration that carries a V1 (Poseidon-based) compiled class hash. The enforcement check in `DeclareTransaction::run_execute` is additionally gated on `self.version() >= TransactionVersion::THREE`, so a `DeclareTransaction::V2` carrying a V1 CASM hash silently bypasses the restriction and commits the wrong hash to state.

### Finding Description

In `crates/blockifier/src/transaction/transactions.rs` lines 176–190, the `Executable::run_execute` implementation for `DeclareTransaction` matches both `V2` and `V3` variants in a single `|`-arm, but the `block_casm_hash_v1_declares` guard is only evaluated when `self.version() >= TransactionVersion::THREE`:

```rust
starknet_api::transaction::DeclareTransaction::V2(DeclareTransactionV2 {
    compiled_class_hash, ..
})
| starknet_api::transaction::DeclareTransaction::V3(DeclareTransactionV3 {
    compiled_class_hash, ..
}) => {
    if context.tx_context.block_context.versioned_constants.block_casm_hash_v1_declares
        && self.version() >= TransactionVersion::THREE   // ← V2 is never ≥ THREE
    {
        self.check_compile_class_hash_v2_declaration()?
    }
    try_declare(self, state, class_hash, Some(*compiled_class_hash))?
}
```

`DeclareTransaction::V2` returns `TransactionVersion::TWO` from `version()`, so the condition is always `false` for V2 transactions. `check_compile_class_hash_v2_declaration` — which verifies that the supplied `compiled_class_hash` equals the Blake-based (V2) hash of the contract — is never called, and `try_declare` stores whatever hash the caller supplied.

V2 declare transactions are accepted by the native blockifier Python wrapper in `crates/native_blockifier/src/py_declare.rs` (lines 124–127):

```rust
} else if version == Felt::TWO {
    let py_declare_tx: PyDeclareTransactionV2 = py_tx.extract()?;
    let declare_tx = DeclareTransactionV2::try_from(py_declare_tx)?;
    Ok(starknet_api::transaction::DeclareTransaction::V2(declare_tx))
```

The `compiled_class_hash` field of `PyDeclareTransactionV2` is taken verbatim from the Python caller with no hash-version validation, so an attacker can supply a Poseidon-based (V1) hash.

The existing test suite confirms the gap: `test_declare_tx` in `crates/blockifier/src/transaction/transactions_test.rs` (lines 1875–1880) has a `#[should_panic(expected = "DeclareTransactionCasmHashMissMatch")]` case only for `TransactionVersion::THREE` with `HashVersion::V1`; there is no analogous rejection case for `TransactionVersion::TWO` with `HashVersion::V1`.

### Impact Explanation

When `block_casm_hash_v1_declares = true` (v0.14.1+) and `enable_casm_hash_migration = true`, the invariant is that every newly declared Cairo 1 class must carry a Blake-based (V2) compiled class hash. A V2 declare transaction with a Poseidon-based (V1) hash violates this invariant and stores the wrong `compiled_class_hash` in state via `try_declare`. This produces a wrong storage value / class hash in the committed state diff, which is a **Critical** impact under the "Wrong state, class hash, or storage value from blockifier/syscall/execution logic for accepted input" criterion. Downstream, the OS CASM-hash migration (`migrate_classes_to_v2_casm_hash` in `crates/apollo_starknet_os_program/src/cairo/starkware/starknet/core/os/os_utils.cairo`) may not cover classes declared after migration is considered complete, leaving a permanent V1 hash in state that diverges from what the OS expects when executing the class.

### Likelihood Explanation

The trigger is unprivileged: any user of the Python-based sequencer (which uses the native blockifier) can submit a V2 declare transaction with an arbitrary `compiled_class_hash`. The `block_casm_hash_v1_declares` flag is `true` in all production versioned-constants files from v0.14.1 onward (`crates/blockifier/resources/blockifier_versioned_constants_0_14_1.json` through `_0_14_4.json`). No additional privilege or special network position is required.

### Recommendation

Extend the version gate to include V2:

```diff
- if context.tx_context.block_context.versioned_constants.block_casm_hash_v1_declares
-     && self.version() >= TransactionVersion::THREE
+ if context.tx_context.block_context.versioned_constants.block_casm_hash_v1_declares
+     && self.version() >= TransactionVersion::TWO
  {
      self.check_compile_class_hash_v2_declaration()?
  }
```

Add a corresponding `#[should_panic(expected = "DeclareTransactionCasmHashMissMatch")]` test case for `TransactionVersion::TWO` with `HashVersion::V1` in `test_declare_tx`.

### Proof of Concept

Using the native blockifier Python API with a block context where `block_casm_hash_v1_declares = true`:

1. Compile a Cairo 1 contract and compute both its V1 (Poseidon) and V2 (Blake) compiled class hashes.
2. Construct a `DeclareTransaction` with `version = 0x2` and `compiled_class_hash = <V1_hash>`.
3. Call `py_block_executor.add_tx(tx, class_info)` — the transaction executes without error.
4. Read back the `compiled_class_hash` from state for the declared class hash; it equals the V1 hash, not the V2 hash.
5. Repeat with `version = 0x3` and the same V1 hash — the transaction is correctly rejected with `DeclareTransactionCasmHashMissMatch`.

The divergence is exact: V3 is rejected, V2 is accepted, and the wrong hash is written to state. [1](#0-0) [2](#0-1) [3](#0-2) [4](#0-3) [5](#0-4)

### Citations

**File:** crates/blockifier/src/transaction/transactions.rs (L176-190)
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
            }
```

**File:** crates/native_blockifier/src/py_declare.rs (L124-127)
```rust
    } else if version == Felt::TWO {
        let py_declare_tx: PyDeclareTransactionV2 = py_tx.extract()?;
        let declare_tx = DeclareTransactionV2::try_from(py_declare_tx)?;
        Ok(starknet_api::transaction::DeclareTransaction::V2(declare_tx))
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

**File:** crates/blockifier/resources/blockifier_versioned_constants_0_14_1.json (L1-2)
```json
{
    "tx_event_limits": {
```

**File:** crates/blockifier/src/transaction/transactions_test.rs (L1875-1880)
```rust
#[should_panic(expected = "DeclareTransactionCasmHashMissMatch")]
#[case(
    TransactionVersion::THREE,
    CairoVersion::Cairo1(RunnableCairo1::Casm),
    Some(HashVersion::V1)
)]
```
