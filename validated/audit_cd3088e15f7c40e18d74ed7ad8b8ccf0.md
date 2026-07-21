### Title
Protobuf `ValidResourceBounds` Deserialization Silently Downgrades `AllResources` to `L1Gas`, Producing a Different Transaction Hash and Wrong Execution Result — (File: `crates/apollo_protobuf/src/converters/transaction.rs`)

---

### Summary

The `TryFrom<protobuf::ResourceBounds> for ValidResourceBounds` implementation in `crates/apollo_protobuf/src/converters/transaction.rs` silently converts `ValidResourceBounds::AllResources` to `ValidResourceBounds::L1Gas` whenever both `l2_gas` and `l1_data_gas` are zero. Because `get_tip_resource_bounds_hash` hashes a **different number of elements** for each variant, the transaction hash recomputed after a protobuf round-trip diverges from the hash the user originally signed. Any node that re-executes the transaction from the deserialized form will pass the wrong hash to the account's `__validate__` entry point, causing signature verification to fail and the transaction to revert incorrectly — a state divergence from the sequencer that executed it correctly.

---

### Finding Description

**Root cause — the silent downgrade**

`crates/apollo_protobuf/src/converters/transaction.rs` lines 417–436:

```rust
impl TryFrom<protobuf::ResourceBounds> for ValidResourceBounds {
    fn try_from(value: protobuf::ResourceBounds) -> Result<Self, Self::Error> {
        // TODO(Shahak): Assert data gas is not none once we remove support for 0.13.2.
        let l1_data_gas = value.l1_data_gas.unwrap_or_default();   // silently defaults to zero
        ...
        Ok(if l1_data_gas.is_zero() && l2_gas.is_zero() {
            ValidResourceBounds::L1Gas(l1_gas)          // ← silent downgrade
        } else {
            ValidResourceBounds::AllResources(AllResourceBounds { l1_gas, l2_gas, l1_data_gas })
        })
    }
}
```

When a sender serialises `AllResources { l1_gas: X, l2_gas: 0, l1_data_gas: 0 }` to protobuf (all three fields are emitted as `Some(zero)`), the receiver deserialises it as `L1Gas(X)`.

**Why the hash differs**

`get_tip_resource_bounds_hash` in `crates/starknet_api/src/transaction_hash.rs` lines 188–211 branches on the variant:

```rust
let mut resource_felts = vec![
    get_concat_resource(&l1_resource_bounds, L1_GAS)?,
    get_concat_resource(&l2_resource_bounds, L2_GAS)?,
];
resource_felts.extend(match resource_bounds {
    ValidResourceBounds::L1Gas(_) => vec![],                          // 2 elements
    ValidResourceBounds::AllResources(all) =>
        vec![get_concat_resource(&all.l1_data_gas, L1_DATA_GAS)?],   // 3 elements
});
Ok(HashChain::new().chain(&tip.0.into()).chain_iter(resource_felts.iter()).get_poseidon_hash())
```

`get_concat_resource` encodes the **resource-name bytes** (`b"L1_DATA"`) into the felt, so the third element is non-zero even when the amount and price are zero. Therefore:

```
hash_AllResources = poseidon(tip, l1_felt, l2_felt=0, l1_data_felt≠0)
hash_L1Gas        = poseidon(tip, l1_felt, l2_felt=0)
```

These are distinct values.

**How the divergence is triggered**

1. `RpcInvokeTransactionV3` always carries `AllResourceBounds` (never `L1Gas`). A user submits `AllResourceBounds { l1_gas: X, l2_gas: 0, l1_data_gas: 0 }` via the JSON-RPC gateway.
2. The gateway converts it to `InvokeTransactionV3` with `ValidResourceBounds::AllResources` (`crates/starknet_api/src/rpc_transaction.rs` line 571) and computes `tx_hash = hash_AllResources`. The user signs this hash.
3. The sequencer executes the transaction correctly; the account's `__validate__` receives `hash_AllResources` and the signature passes.
4. The block is propagated via P2P. `TryFrom<protobuf::InvokeV3> for InvokeTransactionV3` (`crates/apollo_protobuf/src/converters/transaction.rs` line 596) calls `ValidResourceBounds::try_from(...)`, which returns `L1Gas(X)`.
5. A syncing node that re-executes the transaction (e.g., for state validation or reexecution) calls `calculate_transaction_hash` on the deserialized `InvokeTransactionV3`, obtaining `hash_L1Gas ≠ hash_AllResources`.
6. The OS passes `hash_L1Gas` to `__validate__`. The account verifies the signature against `hash_L1Gas`, which does not match the user's signature over `hash_AllResources`. Signature verification fails; the transaction reverts.
7. The syncing node records a revert where the sequencer recorded success — state divergence.

The stateless validator explicitly accepts `AllResourceBounds { l1_gas: NON_EMPTY, l2_gas: 0, l1_data_gas: 0 }` as valid (test case `valid_l1_gas` in `crates/apollo_gateway/src/stateless_transaction_validator_test.rs` lines 70–82), confirming the gateway admits such transactions.

---

### Impact Explanation

**Critical — Wrong execution result for accepted input.**

A transaction that was correctly accepted and executed by the sequencer will revert on every syncing node that re-executes it from the P2P-synced form. The resulting state root on syncing nodes diverges from the canonical state root produced by the sequencer. Downstream effects include incorrect receipts, wrong event logs, and broken fee accounting for the affected transaction.

---

### Likelihood Explanation

**Medium.** The trigger condition — `AllResourceBounds` with non-zero `l1_gas` but zero `l2_gas` and `l1_data_gas` — is explicitly accepted by the gateway's stateless validator. A user who sets only `l1_gas` (e.g., to minimise fees or to replicate a pre-0.13.3 transaction shape) satisfies the condition. The transaction must also pass blockifier pre-validation (minimal L2 gas must be zero or the block context must not enforce it), which narrows but does not eliminate the reachable set. The bug requires no privileged access and is reachable by any unprivileged user.

---

### Recommendation

Remove the silent downgrade. When all three protobuf fields are present (even if zero), always deserialise as `AllResources`. The existing `TODO(Shahak)` comment at line 426 already acknowledges the intent to enforce this once legacy 0.13.2 support is dropped:

```rust
// Replace:
let l1_data_gas = value.l1_data_gas.unwrap_or_default();
...
Ok(if l1_data_gas.is_zero() && l2_gas.is_zero() {
    ValidResourceBounds::L1Gas(l1_gas)
} else {
    ValidResourceBounds::AllResources(...)
})

// With:
let l1_data_gas = value.l1_data_gas.ok_or(missing("ResourceBounds::l1_data_gas"))?;
...
Ok(ValidResourceBounds::AllResources(AllResourceBounds { l1_gas, l2_gas, l1_data_gas }))
```

Add a round-trip test that serialises `AllResources { l1_gas: X, l2_gas: 0, l1_data_gas: 0 }` to protobuf and asserts the deserialised variant is still `AllResources`, and that `calculate_transaction_hash` returns the same value before and after the round-trip.

---

### Proof of Concept

```
1. Craft RpcInvokeTransactionV3 with:
     resource_bounds = AllResourceBounds {
         l1_gas:      ResourceBounds { max_amount: 1000, max_price_per_unit: 1 },
         l2_gas:      ResourceBounds::default(),   // zero
         l1_data_gas: ResourceBounds::default(),   // zero
     }
   Sign the transaction hash H_all = poseidon(tip, l1_felt, l2_felt=0, l1_data_felt≠0).

2. Submit via starknet_addInvokeTransaction. Gateway accepts (stateless validator
   test valid_l1_gas confirms this). Sequencer executes; __validate__ receives H_all;
   signature passes; transaction succeeds.

3. Block is propagated via P2P. Receiving node calls:
     ValidResourceBounds::try_from(protobuf::ResourceBounds { l1_gas: X, l2_gas: 0, l1_data_gas: 0 })
   → returns L1Gas(X)  [line 431 of transaction.rs]

4. Receiving node re-executes the transaction. Blockifier calls
     get_invoke_transaction_v3_hash(...)
   → get_tip_resource_bounds_hash with L1Gas → 2-element poseidon
   → H_l1 = poseidon(tip, l1_felt, l2_felt=0)  ≠  H_all

5. OS passes H_l1 to __validate__. Account verifies signature(H_l1) against
   the user's signature over H_all → FAIL → transaction reverts.

6. Syncing node state diverges from sequencer state.
```

**Key code locations:**

- Silent downgrade: [1](#0-0) 
- Hash variant branch: [2](#0-1) 
- Invoke V3 hash entry point: [3](#0-2) 
- Protobuf InvokeV3 deserialization calling the downgrade: [4](#0-3) 
- RPC always produces AllResources: [5](#0-4) 
- Gateway accepts zero l2/l1_data bounds: [6](#0-5)

### Citations

**File:** crates/apollo_protobuf/src/converters/transaction.rs (L426-436)
```rust
        // TODO(Shahak): Assert data gas is not none once we remove support for 0.13.2.
        let l1_data_gas = value.l1_data_gas.unwrap_or_default();
        let l1_gas: ResourceBounds = l1_gas.try_into()?;
        let l2_gas: ResourceBounds = l2_gas.try_into()?;
        let l1_data_gas: ResourceBounds = l1_data_gas.try_into()?;
        Ok(if l1_data_gas.is_zero() && l2_gas.is_zero() {
            ValidResourceBounds::L1Gas(l1_gas)
        } else {
            ValidResourceBounds::AllResources(AllResourceBounds { l1_gas, l2_gas, l1_data_gas })
        })
    }
```

**File:** crates/apollo_protobuf/src/converters/transaction.rs (L593-660)
```rust
impl TryFrom<protobuf::InvokeV3> for InvokeTransactionV3 {
    type Error = ProtobufConversionError;
    fn try_from(value: protobuf::InvokeV3) -> Result<Self, Self::Error> {
        let resource_bounds = ValidResourceBounds::try_from(
            value.resource_bounds.ok_or(missing("InvokeV3::resource_bounds"))?,
        )?;

        let tip = Tip(value.tip);

        let signature = TransactionSignature(
            value
                .signature
                .ok_or(missing("InvokeV3::signature"))?
                .parts
                .into_iter()
                .map(Felt::try_from)
                .collect::<Result<Vec<_>, _>>()?
                .into(),
        );

        let nonce = Nonce(value.nonce.ok_or(missing("InvokeV3::nonce"))?.try_into()?);

        let sender_address = value.sender.ok_or(missing("InvokeV3::sender"))?.try_into()?;

        let calldata =
            value.calldata.into_iter().map(Felt::try_from).collect::<Result<Vec<_>, _>>()?;

        let calldata = Calldata(calldata.into());

        let nonce_data_availability_mode =
            enum_int_to_volition_domain(value.nonce_data_availability_mode)?;

        let fee_data_availability_mode =
            enum_int_to_volition_domain(value.fee_data_availability_mode)?;

        let paymaster_data = PaymasterData(
            value.paymaster_data.into_iter().map(Felt::try_from).collect::<Result<Vec<_>, _>>()?,
        );

        let account_deployment_data = AccountDeploymentData(
            value
                .account_deployment_data
                .into_iter()
                .map(Felt::try_from)
                .collect::<Result<Vec<_>, _>>()?,
        );

        let proof_facts: ProofFacts = value
            .proof_facts
            .into_iter()
            .map(Felt::try_from)
            .collect::<Result<Vec<_>, _>>()?
            .into();

        Ok(Self {
            resource_bounds,
            tip,
            signature,
            nonce,
            sender_address,
            calldata,
            nonce_data_availability_mode,
            fee_data_availability_mode,
            paymaster_data,
            account_deployment_data,
            proof_facts,
        })
    }
```

**File:** crates/starknet_api/src/transaction_hash.rs (L188-211)
```rust
pub fn get_tip_resource_bounds_hash(
    resource_bounds: &ValidResourceBounds,
    tip: &Tip,
) -> Result<Felt, StarknetApiError> {
    let l1_resource_bounds = resource_bounds.get_l1_bounds();
    let l2_resource_bounds = resource_bounds.get_l2_bounds();

    // L1 and L2 gas bounds always exist.
    // Old V3 txs always have L2 gas bounds of zero, but they exist.
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
}
```

**File:** crates/starknet_api/src/transaction_hash.rs (L370-404)
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
```

**File:** crates/starknet_api/src/rpc_transaction.rs (L568-583)
```rust
impl From<RpcInvokeTransactionV3> for InvokeTransactionV3 {
    fn from(tx: RpcInvokeTransactionV3) -> Self {
        Self {
            resource_bounds: ValidResourceBounds::AllResources(tx.resource_bounds),
            tip: tx.tip,
            signature: tx.signature,
            nonce: tx.nonce,
            sender_address: tx.sender_address,
            calldata: tx.calldata,
            nonce_data_availability_mode: tx.nonce_data_availability_mode,
            fee_data_availability_mode: tx.fee_data_availability_mode,
            paymaster_data: tx.paymaster_data,
            account_deployment_data: tx.account_deployment_data,
            proof_facts: tx.proof_facts,
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
