### Title
`starknet_estimateFee` and `starknet_simulateTransactions` Return Authoritative Fee Estimates for Declare Transactions Blocked by Gateway `block_declare` Flag — (File: `crates/apollo_rpc/src/v0_8/api/api_impl.rs`)

---

### Summary

The gateway enforces a `block_declare` configuration flag that rejects all declare transactions at admission. However, the RPC node's `starknet_estimateFee` and `starknet_simulateTransactions` endpoints bypass the gateway entirely and return valid, authoritative-looking fee estimates for those same declare transactions. This is the direct sequencer analog of the ERC4626 bug: `addDeclareTransaction` (the "deposit") reverts, but `estimateFee` (the "maxDeposit") still returns a non-zero, success-implying value.

---

### Finding Description

**Blocked path — `addDeclareTransaction`:**

`GatewayStaticConfig` carries a `block_declare: bool` field. [1](#0-0)  When `true`, `check_declare_permissions()` in the gateway immediately returns `BLOCKED_TRANSACTION_TYPE` before any further processing. [2](#0-1) 

**Unblocked path — `estimateFee` / `simulateTransactions`:**

`estimate_fee()` in `api_impl.rs` converts the incoming `BroadcastedTransaction` list directly to executable transactions and dispatches them to `exec_estimate_fee()` without consulting the gateway or its config. [3](#0-2) 

`simulate_transactions()` follows the same pattern. [4](#0-3) 

Neither endpoint calls `check_declare_permissions()` or reads `GatewayStaticConfig`. The same asymmetry applies to `authorized_declarer_accounts`: when a whitelist is configured, `estimateFee` still returns a valid fee for an unauthorized sender. [5](#0-4) 

The divergence is structural: `add_declare_transaction` in the RPC layer forwards to `writer_client` (the gateway), [6](#0-5)  while `estimate_fee` and `simulate_transactions` go directly to the blockifier via `exec_estimate_fee` / `exec_simulate_transactions`, completely bypassing the gateway policy layer.

---

### Impact Explanation

`starknet_estimateFee` is the canonical pre-flight check used by wallets, SDKs, and smart-contract integrators to determine whether a transaction will succeed and what it will cost. When `block_declare = true` (or a declarer whitelist is active), `estimateFee` returns a well-formed, non-zero fee response — an authoritative signal that the transaction is executable. The subsequent `addDeclareTransaction` call is then rejected with `BLOCKED_TRANSACTION_TYPE`. The fee estimation endpoint returns an authoritative-looking wrong value, matching the **High** impact tier: *"RPC execution, fee estimation, tracing, simulation, or pending view returns an authoritative-looking wrong value."*

---

### Likelihood Explanation

`block_declare` defaults to `false` in the shipped configs, [7](#0-6)  but it is a documented, production-facing operational lever. [8](#0-7)  Any operator who activates it to pause declares (e.g., during a protocol upgrade) immediately creates the divergence. The `authorized_declarer_accounts` whitelist path is always active when set and affects every non-whitelisted sender. Both triggers are unprivileged from the caller's perspective — any user can call `estimateFee`.

---

### Recommendation

1. `estimate_fee` and `simulate_transactions` in `api_impl.rs` should propagate gateway admission policy to callers. The simplest approach is to add a pre-check that mirrors `check_declare_permissions()` before dispatching to the blockifier when the transaction type is `Declare`.
2. Alternatively, expose a dedicated "admission dry-run" path through the gateway so that `estimateFee` can reflect both execution cost *and* policy eligibility in a single call.
3. At minimum, document clearly in the RPC spec that `estimateFee` does not reflect gateway-level policy restrictions such as `block_declare` or `authorized_declarer_accounts`.

---

### Proof of Concept

```
# Step 1 – enable the block
gateway_config.static_config.block_declare = true

# Step 2 – fee estimation succeeds (wrong signal)
POST starknet_estimateFee
  request: [{ type: "DECLARE", version: "0x3", ... }]
→ 200 OK: { "gas_consumed": "0x...", "overall_fee": "0x..." }
  # Caller infers: transaction is valid and will be accepted.

# Step 3 – submission is rejected (actual behavior)
POST starknet_addDeclareTransaction
  { type: "DECLARE", version: "0x3", ... }
→ Error 40: {
    "code": "StarknetErrorCode.BLOCKED_TRANSACTION_TYPE",
    "message": "Transaction type is temporarily blocked."
  }
```

The exact divergent values are:
- `estimateFee` → `{ overall_fee: N > 0 }` (implies success)
- `addDeclareTransaction` → `BLOCKED_TRANSACTION_TYPE` (actual rejection)

This is the precise sequencer analog of the ERC4626 pattern: `deposit()` reverts while `maxDeposit()` returns `uint256.max`.

### Citations

**File:** crates/apollo_gateway_config/src/config.rs (L80-85)
```rust
        let mut dump = BTreeMap::from_iter([ser_param(
            "block_declare",
            &self.block_declare,
            "If true, the gateway will block declare transactions.",
            ParamPrivacyInput::Public,
        )]);
```

**File:** crates/apollo_gateway/src/gateway.rs (L407-419)
```rust
    fn check_declare_permissions(
        &self,
        declare_tx: &RpcDeclareTransaction,
    ) -> Result<(), StarknetError> {
        // TODO(noamsp): Return same error as in Python gateway.
        if self.config.static_config.block_declare {
            return Err(StarknetError {
                code: StarknetErrorCode::UnknownErrorCode(
                    "StarknetErrorCode.BLOCKED_TRANSACTION_TYPE".to_string(),
                ),
                message: "Transaction type is temporarily blocked.".to_string(),
            });
        }
```

**File:** crates/apollo_gateway/src/gateway.rs (L420-432)
```rust
        let RpcDeclareTransaction::V3(declare_v3_tx) = declare_tx;
        if !self.config.is_authorized_declarer(&declare_v3_tx.sender_address) {
            return Err(StarknetError {
                code: StarknetErrorCode::KnownErrorCode(
                    KnownStarknetErrorCode::UnauthorizedDeclare,
                ),
                message: format!(
                    "Account address {} is not allowed to declare contracts.",
                    &declare_v3_tx.sender_address
                ),
            });
        }
        Ok(())
```

**File:** crates/apollo_rpc/src/v0_8/api/api_impl.rs (L978-995)
```rust
    async fn add_declare_transaction(
        &self,
        declare_transaction: BroadcastedDeclareTransaction,
    ) -> RpcResult<AddDeclareOkResult> {
        let result = self
            .writer_client
            .add_declare_transaction(
                &declare_transaction.try_into().map_err(internal_server_error)?,
            )
            .await;
        match result {
            Ok(res) => Ok(res.into()),
            Err(WriterClientError::ClientError(ClientError::StarknetError(starknet_error))) => {
                Err(ErrorObjectOwned::from(starknet_error_to_declare_error(starknet_error)))
            }
            Err(err) => Err(internal_server_error(err)),
        }
    }
```

**File:** crates/apollo_rpc/src/v0_8/api/api_impl.rs (L1018-1048)
```rust
        let executable_txns =
            transactions.into_iter().map(|tx| tx.try_into()).collect::<Result<_, _>>()?;

        let block_number = get_accepted_block_number(&storage_txn, block_id)?;
        let block_not_reverted_validator =
            BlockNotRevertedValidator::new(block_number, &storage_txn)?;
        drop(storage_txn);
        let state_number = StateNumber::unchecked_right_after_block(block_number);
        let execution_config = self.execution_config;

        let chain_id = self.chain_id.clone();
        let reader = self.storage_reader.clone();
        let class_manager_client =
            create_class_manager_client(self.class_manager_client.clone()).await;

        let estimate_fee_result = tokio::task::spawn_blocking(move || {
            exec_estimate_fee(
                executable_txns,
                &chain_id,
                reader,
                maybe_pending_data,
                state_number,
                block_number,
                &execution_config,
                validate,
                DONT_IGNORE_L1_DA_MODE,
                class_manager_client,
            )
        })
        .await
        .map_err(internal_server_error)?;
```

**File:** crates/apollo_rpc/src/v0_8/api/api_impl.rs (L1074-1121)
```rust
        let executable_txns =
            transactions.into_iter().map(|tx| tx.try_into()).collect::<Result<_, _>>()?;

        let storage_txn = self.storage_reader.begin_ro_txn().map_err(internal_server_error)?;

        let maybe_pending_data = if let BlockId::Tag(Tag::Pending) = block_id {
            Some(client_pending_data_to_execution_pending_data(
                read_pending_data(&self.pending_data, &storage_txn).await?,
                self.pending_classes.read().await.clone(),
            ))
        } else {
            None
        };

        let block_number = get_accepted_block_number(&storage_txn, block_id)?;
        let block_not_reverted_validator =
            BlockNotRevertedValidator::new(block_number, &storage_txn)?;
        drop(storage_txn);
        let state_number = StateNumber::unchecked_right_after_block(block_number);
        let execution_config = self.execution_config;

        let chain_id = self.chain_id.clone();
        let reader = self.storage_reader.clone();

        let charge_fee = !simulation_flags.contains(&SimulationFlag::SkipFeeCharge);
        let validate = !simulation_flags.contains(&SimulationFlag::SkipValidate);
        let class_manager_client =
            create_class_manager_client(self.class_manager_client.clone()).await;

        let simulation_results = tokio::task::spawn_blocking(move || {
            exec_simulate_transactions(
                executable_txns,
                None,
                &chain_id,
                reader,
                maybe_pending_data,
                state_number,
                block_number,
                &execution_config,
                charge_fee,
                validate,
                DONT_IGNORE_L1_DA_MODE,
                class_manager_client,
            )
        })
        .await
        .map_err(internal_server_error)?
        .map_err(execution_error_to_error_object_owned)?;
```

**File:** crates/apollo_deployments/resources/app_configs/gateway_config.json (L4-4)
```json
  "gateway_config.static_config.block_declare": false,
```
