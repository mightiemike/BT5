### Title
`py_deploy_account` Accepts Caller-Supplied `contract_address` Without Canonicalization Verification - (File: crates/native_blockifier/src/py_deploy_account.rs)

### Summary

`py_deploy_account` in `native_blockifier` constructs an executable `DeployAccountTransaction` by reading `contract_address` directly from the Python object's `sender_address` attribute, without recomputing it from the transaction's own `class_hash`, `contract_address_salt`, and `constructor_calldata`. This breaks the canonicalization invariant that `contract_address` must equal `calculate_contract_address(class_hash, salt, constructor_calldata, deployer=0)`. A deploy account transaction carrying a `sender_address` that does not match the deterministic address will be accepted and executed by the blockifier with the wrong `contract_address`, producing wrong state, wrong nonce accounting, and wrong fee charging.

### Finding Description

The Starknet deploy-account hash preimage commits to the **computed** contract address (derived deterministically from `class_hash`, `contract_address_salt`, and `constructor_calldata` with `deployer_address = 0`). This is enforced in every other construction path:

- `DeployAccountTransaction::create` recomputes both `contract_address` and `tx_hash` from the transaction fields.
- `TransactionConverter::convert_rpc_tx_to_internal` calls `tx.calculate_contract_address()` before storing the address.

However, `py_deploy_account` in `native_blockifier` does neither:

```rust
let tx_hash = TransactionHash(py_attr::<PyFelt>(py_tx, "hash_value")?.0);
let contract_address =
    ContractAddress::try_from(py_attr::<PyFelt>(py_tx, "sender_address")?.0)?;
Ok(DeployAccountTransaction { tx, tx_hash, contract_address })
```

Both `tx_hash` and `contract_address` are taken verbatim from the Python object. There is no call to `tx.calculate_contract_address()` and no assertion that the provided value equals the deterministic result. The `tx` inner struct (containing `class_hash`, `contract_address_salt`, `constructor_calldata`) is fully populated and available, so the check is trivially possible but absent.

The analog to the external report is exact: just as `_debitFrom` accepted `_from` without verifying that `msg.sender` owns the token, `py_deploy_account` accepts `contract_address` from the Python caller without verifying that it matches the hash-committed address derived from the transaction's own fields.

### Impact Explanation

When the blockifier executes a `DeployAccountTransaction` with a wrong `contract_address`:

1. The constructor is deployed at the wrong address, corrupting state.
2. `check_and_increment_nonce` operates on the wrong address.
3. Fee charging (`sender_address` in `CommonAccountFields`) targets the wrong account.
4. `tx_info.account_contract_address` exposed to the executing contract is wrong, breaking any contract logic that reads it.
5. The resulting storage diff, receipt, and events all reference the wrong address.

This matches the Critical impact scope: **Wrong state, receipt, event, storage value, or revert result from blockifier/syscall/execution logic for accepted input.**

### Likelihood Explanation

The Python sequencer reads `sender_address` from the transaction JSON submitted by the user. The `IntermediateDeployAccountTransaction` struct in `apollo_starknet_client` carries a `sender_address` field (with an alias `contract_address` for legacy compatibility) that is deserialized directly from the wire format. If the Python sequencer passes this user-supplied value to `py_deploy_account` without recomputing it, any user can submit a deploy account transaction with an arbitrary `sender_address` and have the blockifier execute it against that address. No special privilege is required beyond submitting a transaction.

### Recommendation

Replace the verbatim copy with a canonical recomputation, mirroring `DeployAccountTransaction::create`:

```rust
let contract_address = tx.calculate_contract_address()
    .map_err(|e| NativeBlockifierInputError::InvalidTransactionInput { ... })?;
// Optionally assert equality with the Python-provided value for debugging:
// assert_eq!(contract_address, provided_address);
let tx_hash = tx.calculate_transaction_hash(chain_id, &tx.version())?;
Ok(DeployAccountTransaction { tx, tx_hash, contract_address })
```

### Proof of Concept

1. Construct a valid `DeployAccountTransactionV3` with fields `(class_hash=H, salt=S, constructor_calldata=C)`. The canonical address is `A = calculate_contract_address(S, H, C, 0)`.
2. Craft a Python transaction object where `sender_address = A'` (any address ≠ A) and `hash_value` is the hash computed over `A` (the correct hash).
3. Call `py_deploy_account(py_tx)`. The function returns `DeployAccountTransaction { tx, tx_hash, contract_address: A' }`.
4. Pass this to `blockifier.add_tx`. The blockifier deploys the constructor at `A'`, increments the nonce at `A'`, and charges fees from `A'`, while the signature was over the hash that commits to `A`. The resulting state diff records `A'` as the deployed address, diverging from what the transaction hash canonically identifies. [1](#0-0) [2](#0-1) [3](#0-2) [4](#0-3) [5](#0-4)

### Citations

**File:** crates/native_blockifier/src/py_deploy_account.rs (L83-104)
```rust
pub fn py_deploy_account(py_tx: &PyAny) -> NativeBlockifierResult<DeployAccountTransaction> {
    let version = py_attr::<PyFelt>(py_tx, "version")?.0;
    // TODO(Dori): Make TransactionVersion an enum and use match here.
    let tx = if version == Felt::ONE {
        let py_deploy_account_tx: PyDeployAccountTransactionV1 = py_tx.extract()?;
        let deploy_account_tx = DeployAccountTransactionV1::from(py_deploy_account_tx);
        Ok(starknet_api::transaction::DeployAccountTransaction::V1(deploy_account_tx))
    } else if version == Felt::THREE {
        let py_deploy_account_tx: PyDeployAccountTransactionV3 = py_tx.extract()?;
        let deploy_account_tx = DeployAccountTransactionV3::try_from(py_deploy_account_tx)?;
        Ok(starknet_api::transaction::DeployAccountTransaction::V3(deploy_account_tx))
    } else {
        Err(NativeBlockifierInputError::UnsupportedTransactionVersion {
            tx_type: TransactionType::DeployAccount,
            version: version.to_biguint(),
        })
    }?;

    let tx_hash = TransactionHash(py_attr::<PyFelt>(py_tx, "hash_value")?.0);
    let contract_address =
        ContractAddress::try_from(py_attr::<PyFelt>(py_tx, "sender_address")?.0)?;
    Ok(DeployAccountTransaction { tx, tx_hash, contract_address })
```

**File:** crates/starknet_api/src/executable_transaction.rs (L323-331)
```rust
    pub fn create(
        deploy_account_tx: crate::transaction::DeployAccountTransaction,
        chain_id: &ChainId,
    ) -> Result<Self, StarknetApiError> {
        let contract_address = deploy_account_tx.calculate_contract_address()?;
        let tx_hash =
            deploy_account_tx.calculate_transaction_hash(chain_id, &deploy_account_tx.version())?;
        Ok(Self { tx: deploy_account_tx, tx_hash, contract_address })
    }
```

**File:** crates/apollo_transaction_converter/src/transaction_converter.rs (L378-392)
```rust
            RpcTransaction::DeployAccount(RpcDeployAccountTransaction::V3(tx)) => {
                let contract_address = tx.calculate_contract_address()?;
                (
                    InternalRpcTransactionWithoutTxHash::DeployAccount(
                        InternalRpcDeployAccountTransaction {
                            tx: RpcDeployAccountTransaction::V3(tx),
                            contract_address,
                        },
                    ),
                    None,
                )
            }
        };
        let tx_hash = tx_without_hash.calculate_transaction_hash(&self.chain_id)?;
        Ok((InternalRpcTransaction { tx: tx_without_hash, tx_hash }, proof_data))
```

**File:** crates/starknet_api/src/transaction_hash.rs (L709-743)
```rust
pub(crate) fn get_deploy_account_transaction_v3_hash<
    T: DeployAccountTransactionV3Trait + CalculateContractAddress,
>(
    transaction: &T,
    chain_id: &ChainId,
    transaction_version: &TransactionVersion,
) -> Result<TransactionHash, StarknetApiError> {
    let contract_address = transaction.calculate_contract_address()?;
    let tip_resource_bounds_hash =
        get_tip_resource_bounds_hash(&transaction.resource_bounds(), transaction.tip())?;
    let paymaster_data_hash =
        HashChain::new().chain_iter(transaction.paymaster_data().0.iter()).get_poseidon_hash();
    let data_availability_mode = concat_data_availability_mode(
        transaction.nonce_data_availability_mode(),
        transaction.fee_data_availability_mode(),
    );
    let constructor_calldata_hash = HashChain::new()
        .chain_iter(transaction.constructor_calldata().0.iter())
        .get_poseidon_hash();

    Ok(TransactionHash(
        HashChain::new()
            .chain(&DEPLOY_ACCOUNT)
            .chain(&transaction_version.0)
            .chain(contract_address.0.key())
            .chain(&tip_resource_bounds_hash)
            .chain(&paymaster_data_hash)
            .chain(&Felt::try_from(chain_id)?)
            .chain(&transaction.nonce().0)
            .chain(&data_availability_mode)
            .chain(&constructor_calldata_hash)
            .chain(&transaction.class_hash().0)
            .chain(&transaction.contract_address_salt().0)
            .get_poseidon_hash(),
    ))
```

**File:** crates/starknet_api/src/transaction.rs (L459-473)
```rust
impl<T: DeployTransactionTrait> CalculateContractAddress for T {
    /// Calculates the contract address for the contract deployed by a deploy account transaction.
    /// For more details see:
    /// <https://docs.starknet.io/learn/cheatsheets/transactions-reference#deploy-account-v3>
    fn calculate_contract_address(&self) -> StarknetApiResult<ContractAddress> {
        // When the contract is deployed via a deploy-account transaction, the deployer address is
        // zero.
        const DEPLOYER_ADDRESS: ContractAddress = ContractAddress(PatriciaKey::ZERO);
        calculate_contract_address(
            self.contract_address_salt(),
            self.class_hash(),
            self.constructor_calldata(),
            DEPLOYER_ADDRESS,
        )
    }
```
