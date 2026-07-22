### Title
`get_tip_resource_bounds_hash` produces divergent hashes for `AllResources{l2=0,l1_data=0}` vs `L1Gas` after protobuf round-trip — (`File: crates/starknet_api/src/transaction_hash.rs`)

### Summary

`get_tip_resource_bounds_hash` branches on the `ValidResourceBounds` enum variant to decide how many resource felts to include in the Poseidon hash preimage. `L1Gas` produces a 2-element preimage; `AllResources` always appends a third element (`L1_DATA_GAS`), even when both `l2_gas` and `l1_data_gas` are zero. The protobuf deserializer (`TryFrom<protobuf::ResourceBounds> for ValidResourceBounds`) collapses `AllResources{l2=0, l1_data=0}` back to `L1Gas`. A transaction submitted via RPC with `AllResourceBounds{l1_gas: X, l2_gas: 0, l1_data_gas: 0}` is hashed at the gateway with the 3-element preimage; after a protobuf round-trip the same transaction is hashed with the 2-element preimage, producing a different `TransactionHash`. The OS hint `assert_transaction_hash` then fails, causing the transaction to be treated as invalid during proof generation.

---

### Finding Description

**Step 1 — Hash preimage length is variant-dependent.**

`get_tip_resource_bounds_hash` unconditionally emits two resource felts (`L1_GAS`, `L2_GAS`) and then conditionally appends a third (`L1_DATA_GAS`) only for `AllResources`:

```
ValidResourceBounds::L1Gas(_)          → [tip, L1_GAS_felt, L2_GAS_felt(0)]          // 3 inputs
ValidResourceBounds::AllResources(…)   → [tip, L1_GAS_felt, L2_GAS_felt(0), L1_DATA_GAS_felt(0)]  // 4 inputs
```

When `l2_gas = 0` and `l1_data_gas = 0` the two variants carry identical numeric data but produce different Poseidon hashes because the input length differs. [1](#0-0) 

**Step 2 — Gateway always hashes with `AllResources`.**

`RpcInvokeTransactionV3` and `InternalRpcInvokeTransactionV3` both carry `resource_bounds: AllResourceBounds` (not `ValidResourceBounds`). Their `InvokeTransactionV3Trait::resource_bounds()` implementation wraps the value in `ValidResourceBounds::AllResources(…)` unconditionally, so the hash computed at ingestion always uses the 4-input preimage. [2](#0-1) [3](#0-2) 

**Step 3 — Protobuf deserializer collapses `AllResources{l2=0,l1_data=0}` to `L1Gas`.**

```rust
Ok(if l1_data_gas.is_zero() && l2_gas.is_zero() {
    ValidResourceBounds::L1Gas(l1_gas)   // ← 2-element preimage on re-hash
} else {
    ValidResourceBounds::AllResources(…)
})
``` [4](#0-3) 

After this conversion, any call to `InvokeTransactionV3::calculate_transaction_hash` on the deserialized object produces the 3-input hash, which differs from the 4-input hash stored in `tx_hash`.

**Step 4 — OS hint enforces hash consistency.**

`assert_transaction_hash` in the SNOS hint layer compares the stored `tx_hash` against the hash recomputed from the transaction fields. A mismatch aborts execution with `"Computed transaction_hash is inconsistent with the hash in the transaction"`. [5](#0-4) 

**Step 5 — The trigger is unprivileged and accepted by the gateway.**

The stateless validator test `valid_l1_gas` confirms that `AllResourceBounds { l1_gas: NON_EMPTY_RESOURCE_BOUNDS, l2_gas: 0, l1_data_gas: 0 }` passes all gateway checks. Any user can submit such a transaction. [6](#0-5) 

---

### Impact Explanation

A transaction submitted via RPC with `AllResourceBounds{l1_gas: X, l2_gas: 0, l1_data_gas: 0}` is accepted, assigned hash **H_all** (4-input Poseidon), and propagated. After protobuf deserialization on any peer or during OS re-execution, the same transaction fields produce hash **H_l1** (3-input Poseidon). The OS hint `assert_transaction_hash` fails, causing the transaction to be treated as having an invalid hash during proof generation. This maps to:

> **Critical. Wrong state, receipt, event, L1 message, class hash, storage value, or revert result from blockifier/syscall/execution logic for accepted input.**

and

> **High. Transaction conversion or signature/hash logic binds the wrong signer, hash, type, or executable payload.**

---

### Likelihood Explanation

The trigger requires only a standard V3 invoke transaction with non-zero L1 gas and zero L2/data gas — a configuration explicitly tested as valid in the gateway. No special privileges, malformed bytes, or adversarial peers are needed. The divergence is deterministic and reproducible on every node that receives the transaction via P2P protobuf.

---

### Recommendation

**Short term:** In `get_tip_resource_bounds_hash`, normalize the variant before branching. Either always emit three resource felts (appending a zero `L1_DATA_GAS` felt for `L1Gas` transactions), or always emit two felts for both variants when `l1_data_gas = 0`. The chosen rule must match the OS Cairo implementation in `hash_fee_fields`.

**Long term:** Remove the `ValidResourceBounds::L1Gas` / `ValidResourceBounds::AllResources` split from the hash path entirely. All V3 transactions should carry `AllResourceBounds` end-to-end (as `RpcInvokeTransactionV3` already does), and the protobuf deserializer should never silently downgrade `AllResources` to `L1Gas` when values happen to be zero.

---

### Proof of Concept

```
1. Construct InvokeV3 with:
     resource_bounds = AllResourceBounds {
         l1_gas:      ResourceBounds { max_amount: 1000, max_price_per_unit: 1 },
         l2_gas:      ResourceBounds { max_amount: 0,    max_price_per_unit: 0 },
         l1_data_gas: ResourceBounds { max_amount: 0,    max_price_per_unit: 0 },
     }

2. Submit via RPC → gateway accepts, computes:
     H_all = Poseidon(tip, L1_GAS_felt, L2_GAS_felt(0), L1_DATA_GAS_felt(0))
   stores InternalRpcTransaction { tx_hash: H_all, … }

3. Transaction propagated via P2P protobuf.
   Receiving node deserializes ResourceBounds:
     l1_data_gas.is_zero() && l2_gas.is_zero() == true
     → ValidResourceBounds::L1Gas(l1_gas)

4. OS re-execution calls assert_transaction_hash:
     stored_hash    = H_all  (4-input Poseidon)
     recomputed     = H_l1   (3-input Poseidon, L1_DATA_GAS_felt omitted)
     H_all ≠ H_l1  → OsHintError::AssertionFailed

5. Transaction is rejected / block proof fails for a legitimately submitted tx.
```

### Citations

**File:** crates/starknet_api/src/transaction_hash.rs (L197-210)
```rust
    let mut resource_felts = vec![
        get_concat_resource(&l1_resource_bounds, L1_GAS)?,
        get_concat_resource(&l2_resource_bounds, L2_GAS)?,
    ];

    // For new V3 txs, need to also hash the data gas bounds.
    resource_felts.extend(match resource_bounds {
        ValidResourceBounds::L1Gas(_) => vec![],
        ValidResourceBounds::AllResources(all_resources) => {
            vec![get_concat_resource(&all_resources.l1_data_gas, L1_DATA_GAS)?]
        }
    });

    Ok(HashChain::new().chain(&tip.0.into()).chain_iter(resource_felts.iter()).get_poseidon_hash())
```

**File:** crates/starknet_api/src/transaction_hash.rs (L370-405)
```rust
pub(crate) fn get_invoke_transaction_v3_hash<T: InvokeTransactionV3Trait>(
    transaction: &T,
    chain_id: &ChainId,
    transaction_version: &TransactionVersion,
) -> Result<TransactionHash, StarknetApiError> {
    let tip_resource_bounds_hash =
        get_tip_resource_bounds_hash(&transaction.resource_bounds(), transaction.tip())?;
    let paymaster_data_hash =
        HashChain::new().chain_iter(transaction.paymaster_data().0.iter()).get_poseidon_hash();
    let data_availability_mode = concat_data_availability_mode(
        transaction.nonce_data_availability_mode(),
        transaction.fee_data_availability_mode(),
    );
    let account_deployment_data_hash = HashChain::new()
        .chain_iter(transaction.account_deployment_data().0.iter())
        .get_poseidon_hash();
    let calldata_hash =
        HashChain::new().chain_iter(transaction.calldata().0.iter()).get_poseidon_hash();
    let mut hash_chain = HashChain::new()
        .chain(&INVOKE)
        .chain(&transaction_version.0)
        .chain(transaction.sender_address().0.key())
        .chain(&tip_resource_bounds_hash)
        .chain(&paymaster_data_hash)
        .chain(&Felt::try_from(chain_id)?)
        .chain(&transaction.nonce().0)
        .chain(&data_availability_mode)
        .chain(&account_deployment_data_hash)
        .chain(&calldata_hash);
    if !transaction.proof_facts().0.is_empty() {
        let proof_facts_hash =
            HashChain::new().chain_iter(transaction.proof_facts().0.iter()).get_poseidon_hash();
        hash_chain = hash_chain.chain(&proof_facts_hash);
    }
    Ok(TransactionHash(hash_chain.get_poseidon_hash()))
}
```

**File:** crates/starknet_api/src/rpc_transaction.rs (L636-639)
```rust
impl InvokeTransactionV3Trait for InternalRpcInvokeTransactionV3 {
    fn resource_bounds(&self) -> ValidResourceBounds {
        ValidResourceBounds::AllResources(self.resource_bounds)
    }
```

**File:** crates/apollo_protobuf/src/converters/transaction.rs (L431-436)
```rust
        Ok(if l1_data_gas.is_zero() && l2_gas.is_zero() {
            ValidResourceBounds::L1Gas(l1_gas)
        } else {
            ValidResourceBounds::AllResources(AllResourceBounds { l1_gas, l2_gas, l1_data_gas })
        })
    }
```

**File:** crates/starknet_os/src/hints/hint_implementation/execution/implementation.rs (L142-161)
```rust
pub(crate) fn assert_transaction_hash<S: StateReader>(
    hint_processor: &mut SnosHintProcessor<'_, S>,
    ctx: HintContext<'_>,
) -> OsHintResult {
    let stored_transaction_hash = ctx.get_integer(Ids::TransactionHash)?;
    let calculated_tx_hash =
        hint_processor.get_current_execution_helper()?.tx_tracker.get_tx()?.tx_hash().0;

    if calculated_tx_hash == stored_transaction_hash {
        Ok(())
    } else {
        Err(OsHintError::AssertionFailed {
            message: format!(
                "Computed transaction_hash is inconsistent with the hash in the transaction. \
                 Computed hash = {stored_transaction_hash:#x}, Expected hash = \
                 {calculated_tx_hash:#x}."
            ),
        })
    }
}
```

**File:** crates/apollo_gateway/src/stateless_transaction_validator_test.rs (L70-82)
```rust
#[case::valid_l1_gas(
    StatelessTransactionValidatorConfig {
        validate_resource_bounds: true,
        ..*DEFAULT_VALIDATOR_CONFIG_FOR_TESTING
    },
    RpcTransactionArgs {
        resource_bounds: AllResourceBounds {
            l1_gas: NON_EMPTY_RESOURCE_BOUNDS,
            ..Default::default()
        },
        ..Default::default()
    }
)]
```
