### Title
`ValidResourceBounds` Protobuf Round-Trip Silently Downgrades `AllResources` to `L1Gas`, Producing a Divergent Transaction Hash During P2P Block Sync — (`File: crates/apollo_protobuf/src/converters/transaction.rs`)

---

### Summary

The protobuf deserializer for `ValidResourceBounds` uses a zero-value heuristic to decide between `L1Gas` and `AllResources`. The transaction hash function `get_tip_resource_bounds_hash` produces structurally different Poseidon preimages for these two variants. A transaction submitted via RPC with `AllResourceBounds` where `l2_gas = 0` and `l1_data_gas = 0` is hashed with three resource elements (including `L1_DATA_GAS`). When the same transaction is later serialized to protobuf and deserialized by a syncing peer, it is reconstructed as `L1Gas`, which is hashed with only two resource elements. The two hashes are irreconcilably different.

---

### Finding Description

**Step 1 — Protobuf deserialization heuristic**

`TryFrom<protobuf::ResourceBounds> for ValidResourceBounds` in `crates/apollo_protobuf/src/converters/transaction.rs` applies:

```rust
Ok(if l1_data_gas.is_zero() && l2_gas.is_zero() {
    ValidResourceBounds::L1Gas(l1_gas)
} else {
    ValidResourceBounds::AllResources(AllResourceBounds { l1_gas, l2_gas, l1_data_gas })
})
``` [1](#0-0) 

This is a lossy round-trip: an `AllResources` value with `l2_gas = 0` and `l1_data_gas = 0` serializes to protobuf with those fields zero, then deserializes back as `L1Gas`.

**Step 2 — Hash preimage diverges on the variant**

`get_tip_resource_bounds_hash` in `crates/starknet_api/src/transaction_hash.rs` conditionally appends `L1_DATA_GAS` only for `AllResources`:

```rust
resource_felts.extend(match resource_bounds {
    ValidResourceBounds::L1Gas(_) => vec![],
    ValidResourceBounds::AllResources(all_resources) => {
        vec![get_concat_resource(&all_resources.l1_data_gas, L1_DATA_GAS)?]
    }
});
Ok(HashChain::new().chain(&tip.0.into()).chain_iter(resource_felts.iter()).get_poseidon_hash())
``` [2](#0-1) 

- `AllResources` with `l2_gas=0, l1_data_gas=0` → Poseidon over `[tip, L1_GAS_packed, L2_GAS_packed(0), L1_DATA_GAS_packed(0)]` (4 elements)
- `L1Gas` with the same l1_gas → Poseidon over `[tip, L1_GAS_packed, L2_GAS_packed(0)]` (3 elements)

These produce different field elements. The transaction hash `H1 ≠ H2`.

**Step 3 — Submission path always uses `AllResources`**

`RpcInvokeTransactionV3` carries `resource_bounds: AllResourceBounds`, which is always wrapped as `ValidResourceBounds::AllResources` when converted to `InternalRpcInvokeTransactionV3`:

```rust
fn resource_bounds(&self) -> ValidResourceBounds {
    ValidResourceBounds::AllResources(self.resource_bounds)
}
``` [3](#0-2) 

The hash `H1` is computed and stored in `InternalRpcTransaction.tx_hash` at gateway ingestion time. [4](#0-3) 

**Step 4 — P2P sync path reconstructs `L1Gas`**

When the block is synced via P2P, `InvokeTransactionV3` (which holds `ValidResourceBounds::AllResources`) is serialized to `protobuf::InvokeV3`. On the receiving peer, `TryFrom<protobuf::InvokeV3> for InvokeTransactionV3` calls the heuristic above and reconstructs `ValidResourceBounds::L1Gas`. Any subsequent call to `calculate_transaction_hash` on this object produces `H2 ≠ H1`. [5](#0-4) 

---

### Impact Explanation

A syncing node that recomputes transaction hashes from the P2P-received `InvokeTransactionV3` body will derive `H2` while the block commitment encodes `H1`. This breaks the hash canonicalization invariant across the RPC submission path and the P2P sync path. Concretely:

- **Block validation failure**: If the syncing node validates transaction hashes against the block's transaction commitment, it will reject valid blocks containing such transactions, causing a liveness failure.
- **Wrong hash stored**: If the syncing node trusts the block-provided hash but later re-derives the hash from the stored transaction body (e.g., for RPC `starknet_getTransactionByHash` or fee estimation), it returns an authoritative-looking wrong value.

This matches: *High. Transaction conversion or signature/hash logic binds the wrong signer, hash, type, or executable payload.*

---

### Likelihood Explanation

Any user can submit an `RpcInvokeTransactionV3` with `AllResourceBounds` where `l2_gas.max_amount = 0`, `l2_gas.max_price_per_unit = 0`, `l1_data_gas.max_amount = 0`, `l1_data_gas.max_price_per_unit = 0`, and `l1_gas` non-zero. The gateway stateless validator accepts this (at least one non-zero resource bound is sufficient). The condition is trivially reachable by any unprivileged submitter. [6](#0-5) 

---

### Recommendation

Replace the zero-value heuristic in the protobuf `ValidResourceBounds` deserializer with an explicit discriminant field in the protobuf schema, or always deserialize to `AllResources` when all three resource bound fields are present (regardless of their values). The current heuristic was introduced to support pre-0.13.3 transactions that only had `L1Gas`, but it incorrectly collapses post-0.13.3 `AllResources` transactions that happen to have zero `l2_gas` and `l1_data_gas`.

Alternatively, ensure that `get_tip_resource_bounds_hash` produces the same output for `L1Gas(x)` and `AllResources { l1_gas: x, l2_gas: 0, l1_data_gas: 0 }` by always including all three resource elements in the hash preimage regardless of the variant.

---

### Proof of Concept

```
1. Submit via RPC:
   RpcInvokeTransactionV3 {
     resource_bounds: AllResourceBounds {
       l1_gas:      { max_amount: 1000, max_price_per_unit: 1 },
       l2_gas:      { max_amount: 0,    max_price_per_unit: 0 },
       l1_data_gas: { max_amount: 0,    max_price_per_unit: 0 },
     },
     ...
   }

2. Gateway computes H1 = Poseidon([INVOKE, version, sender,
     Poseidon([tip, L1_GAS_packed, L2_GAS_packed(0), L1_DATA_GAS_packed(0)]),
     ...])
   (4-element resource hash, AllResources path)

3. Block is produced with tx_hash = H1.

4. Syncing peer receives InvokeV3 protobuf with l2_gas=0, l1_data_gas=0.
   Deserializes as ValidResourceBounds::L1Gas(l1_gas).

5. Syncing peer computes H2 = Poseidon([INVOKE, version, sender,
     Poseidon([tip, L1_GAS_packed, L2_GAS_packed(0)]),
     ...])
   (3-element resource hash, L1Gas path)

6. H1 ≠ H2 → hash mismatch for a valid, accepted transaction.
``` [7](#0-6) [8](#0-7)

### Citations

**File:** crates/apollo_protobuf/src/converters/transaction.rs (L417-436)
```rust
impl TryFrom<protobuf::ResourceBounds> for ValidResourceBounds {
    type Error = ProtobufConversionError;
    fn try_from(value: protobuf::ResourceBounds) -> Result<Self, Self::Error> {
        let Some(l1_gas) = value.l1_gas else {
            return Err(missing("ResourceBounds::l1_gas"));
        };
        let Some(l2_gas) = value.l2_gas else {
            return Err(missing("ResourceBounds::l2_gas"));
        };
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

**File:** crates/starknet_api/src/transaction_hash.rs (L203-210)
```rust
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

**File:** crates/starknet_api/src/rpc_transaction.rs (L637-639)
```rust
    fn resource_bounds(&self) -> ValidResourceBounds {
        ValidResourceBounds::AllResources(self.resource_bounds)
    }
```

**File:** crates/apollo_transaction_converter/src/transaction_converter.rs (L391-392)
```rust
        let tx_hash = tx_without_hash.calculate_transaction_hash(&self.chain_id)?;
        Ok((InternalRpcTransaction { tx: tx_without_hash, tx_hash }, proof_data))
```

**File:** crates/starknet_api/src/transaction/fields.rs (L363-367)
```rust
#[derive(Clone, Copy, Debug, Eq, PartialEq, Hash, Ord, PartialOrd)]
pub enum ValidResourceBounds {
    L1Gas(ResourceBounds), // Pre 0.13.3. Only L1 gas. L2 bounds are signed but never used.
    AllResources(AllResourceBounds),
}
```
