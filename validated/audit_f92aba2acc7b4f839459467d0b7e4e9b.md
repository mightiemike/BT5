### Title
`int_to_chain_id` Bypasses Canonical `ChainId` Variant Matching, Breaking Chain-ID Equality in Deprecated Transaction Hash Gate - (File: `crates/native_blockifier/src/py_utils.rs`)

### Summary

`int_to_chain_id` converts a Python integer chain ID to `ChainId` by calling `biguint.to_bytes_be()` then `String::from_utf8_lossy(...)` and wrapping the result unconditionally in `ChainId::Other(...)`. This bypasses the canonical `ChainId::from(String)` path that maps `"SN_MAIN"` → `ChainId::Mainnet`, `"SN_SEPOLIA"` → `ChainId::Sepolia`, etc. The resulting `ChainId::Other("SN_MAIN")` is structurally unequal to `ChainId::Mainnet`, silently breaking every equality guard that distinguishes mainnet from other chains — most critically the gate in `get_deprecated_transaction_hashes` that suppresses deprecated-hash computation on mainnet after block 1470.

### Finding Description

`int_to_chain_id` in `crates/native_blockifier/src/py_utils.rs`:

```rust
pub fn int_to_chain_id(int: &PyAny) -> PyResult<ChainId> {
    let biguint: BigUint = int.extract()?;
    Ok(ChainId::Other(String::from_utf8_lossy(&biguint.to_bytes_be()).into()))
}
```

The canonical conversion path is `ChainId::from(String)`:

```rust
impl From<String> for ChainId {
    fn from(s: String) -> Self {
        match s.as_ref() {
            "SN_MAIN" => ChainId::Mainnet,
            "SN_SEPOLIA" => ChainId::Sepolia,
            "SN_INTEGRATION_SEPOLIA" => ChainId::IntegrationSepolia,
            other => ChainId::Other(other.to_owned()),
        }
    }
}
```

`int_to_chain_id` never calls this path. For mainnet, Python passes the integer `0x534e5f4d41494e` (big-endian bytes of `"SN_MAIN"`). `to_bytes_be()` yields `[0x53, 0x4e, 0x5f, 0x4d, 0x41, 0x49, 0x4e]`, `from_utf8_lossy` yields `"SN_MAIN"`, but the result is `ChainId::Other("SN_MAIN")`, not `ChainId::Mainnet`.

`ChainId` derives `PartialEq`/`Eq`, so `ChainId::Other("SN_MAIN") != ChainId::Mainnet`.

This breaks the gate in `get_deprecated_transaction_hashes` (`crates/starknet_api/src/transaction_hash.rs`):

```rust
Ok(if chain_id == &ChainId::Mainnet
    && block_number > &MAINNET_TRANSACTION_HASH_WITH_VERSION
{
    vec![]   // ← never reached when chain_id is ChainId::Other("SN_MAIN")
} else {
    match transaction {
        Transaction::Deploy(deploy) => vec![get_deprecated_deploy_transaction_hash(...)],
        Transaction::Invoke(InvokeTransaction::V0(v0)) => vec![get_deprecated_invoke_transaction_v0_hash(...)],
        Transaction::L1Handler(l1) => get_deprecated_l1_handler_transaction_hashes(...)?,
        _ => vec![],
    }
})
```

`MAINNET_TRANSACTION_HASH_WITH_VERSION` is block 1470. After that block, mainnet should return an empty deprecated-hash list. With `ChainId::Other("SN_MAIN")`, the condition is always false, so deprecated hashes are always computed for `Deploy`, `InvokeV0`, and `L1Handler` transactions regardless of block number.

`PyOsConfig` is the entry point that receives the chain ID from Python and feeds it into `PyBlockExecutor::chain_info`, which flows into every `BlockContext` created by the executor:

```rust
#[derive(Clone, FromPyObject)]
pub struct PyOsConfig {
    #[pyo3(from_py_with = "int_to_chain_id")]
    pub chain_id: ChainId,
    ...
}
```

### Impact Explanation

The deprecated-hash list is used in `get_transaction_hash` to validate whether a user-supplied transaction hash is acceptable. When the list is non-empty, a transaction whose hash matches a deprecated format is admitted. With the bug, on mainnet after block 1470, `Deploy`, `InvokeV0`, and `L1Handler` transactions carrying a deprecated-format hash are accepted by the Python-facing block executor when they should be rejected. This is a transaction-admission correctness failure: the Python sequencer accepts transactions that the canonical hash logic would reject, creating a divergence between the Python and Rust execution paths.

Additionally, `String::from_utf8_lossy` silently replaces invalid UTF-8 byte sequences with `U+FFFD`. For any custom chain ID whose byte encoding is not valid UTF-8, the resulting `ChainId` string is silently corrupted, causing every downstream use of that chain ID (transaction hash preimage, OS config hash, `get_execution_info` syscall) to operate on a wrong value.

### Likelihood Explanation

The `int_to_chain_id` function is the sole conversion path for chain IDs entering `PyBlockExecutor` from Python. It is called unconditionally on every executor initialization. Any operator running the Python sequencer on mainnet triggers this path on every block. The condition `chain_id == &ChainId::Mainnet` is evaluated for every `Deploy`, `InvokeV0`, and `L1Handler` transaction processed after block 1470.

### Recommendation

Replace the body of `int_to_chain_id` with the canonical conversion:

```rust
pub fn int_to_chain_id(int: &PyAny) -> PyResult<ChainId> {
    let biguint: BigUint = int.extract()?;
    let bytes = biguint.to_bytes_be();
    let s = std::str::from_utf8(&bytes)
        .map_err(|e| PyValueError::new_err(format!("chain_id bytes are not valid UTF-8: {e}")))?;
    Ok(ChainId::from(s.to_owned()))
}
```

This routes through `ChainId::from(String)`, which correctly maps `"SN_MAIN"` to `ChainId::Mainnet`, `"SN_SEPOLIA"` to `ChainId::Sepolia`, etc., and also rejects non-UTF-8 byte sequences with an explicit error instead of silently corrupting them.

### Proof of Concept

```rust
use num_bigint::BigUint;
use starknet_api::core::ChainId;

fn main() {
    // Python passes 0x534e5f4d41494e (big-endian encoding of "SN_MAIN")
    let mainnet_int = BigUint::from(0x534e5f4d41494e_u64);

    // Current (buggy) int_to_chain_id behaviour:
    let buggy = ChainId::Other(
        String::from_utf8_lossy(&mainnet_int.to_bytes_be()).into()
    );

    // Canonical conversion:
    let canonical = ChainId::from("SN_MAIN".to_string());

    assert_eq!(canonical, ChainId::Mainnet);   // passes
    assert_ne!(buggy, ChainId::Mainnet);        // passes — demonstrates the divergence

    // The gate in get_deprecated_transaction_hashes:
    //   if chain_id == &ChainId::Mainnet && block_number > 1470 { vec![] }
    // is never taken with `buggy`, so deprecated hashes are always computed
    // for Deploy/InvokeV0/L1Handler on mainnet after block 1470.
}
``` [1](#0-0) [2](#0-1) [3](#0-2) [4](#0-3)

### Citations

**File:** crates/native_blockifier/src/py_utils.rs (L88-91)
```rust
pub fn int_to_chain_id(int: &PyAny) -> PyResult<ChainId> {
    let biguint: BigUint = int.extract()?;
    Ok(ChainId::Other(String::from_utf8_lossy(&biguint.to_bytes_be()).into()))
}
```

**File:** crates/starknet_api/src/core.rs (L66-74)
```rust
impl From<String> for ChainId {
    fn from(s: String) -> Self {
        match s.as_ref() {
            "SN_MAIN" => ChainId::Mainnet,
            "SN_SEPOLIA" => ChainId::Sepolia,
            "SN_INTEGRATION_SEPOLIA" => ChainId::IntegrationSepolia,
            other => ChainId::Other(other.to_owned()),
        }
    }
```

**File:** crates/starknet_api/src/transaction_hash.rs (L130-163)
```rust
fn get_deprecated_transaction_hashes(
    chain_id: &ChainId,
    block_number: &BlockNumber,
    transaction: &Transaction,
    transaction_options: &TransactionOptions,
) -> Result<Vec<TransactionHash>, StarknetApiError> {
    let transaction_version = &signed_tx_version_from_tx(transaction, transaction_options);
    Ok(if chain_id == &ChainId::Mainnet && block_number > &MAINNET_TRANSACTION_HASH_WITH_VERSION {
        vec![]
    } else {
        match transaction {
            Transaction::Declare(_) => vec![],
            Transaction::Deploy(deploy) => {
                vec![get_deprecated_deploy_transaction_hash(deploy, chain_id, transaction_version)?]
            }
            Transaction::DeployAccount(_) => vec![],
            Transaction::Invoke(invoke) => match invoke {
                InvokeTransaction::V0(invoke_v0) => {
                    vec![get_deprecated_invoke_transaction_v0_hash(
                        invoke_v0,
                        chain_id,
                        transaction_version,
                    )?]
                }
                InvokeTransaction::V1(_) | InvokeTransaction::V3(_) => vec![],
            },
            Transaction::L1Handler(l1_handler) => get_deprecated_l1_handler_transaction_hashes(
                l1_handler,
                chain_id,
                transaction_version,
            )?,
        }
    })
}
```

**File:** crates/native_blockifier/src/py_block_executor.rs (L475-481)
```rust
#[derive(Clone, FromPyObject)]
pub struct PyOsConfig {
    #[pyo3(from_py_with = "int_to_chain_id")]
    pub chain_id: ChainId,
    pub deprecated_fee_token_address: PyFelt,
    pub fee_token_address: PyFelt,
}
```
