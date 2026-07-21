Looking at the exact code paths involved:

### Title
P2P Sync Client Stores Peer-Supplied `transaction_hash` Verbatim Without Body Verification, Allowing Malicious Peer to Inject `InvokeV3` with Crafted `proof_facts` Causing Hash–Body Inconsistency in Storage — (`crates/apollo_p2p_sync/src/client/transaction.rs`)

---

### Summary

The p2p sync client accepts `FullTransaction` messages from untrusted peers and stores the peer-supplied `transaction_hash` field directly into `BlockBody::transaction_hashes` without recomputing or verifying it against the transaction body. Because `get_invoke_transaction_v3_hash` conditionally appends `proof_facts` to the hash chain only when non-empty, a malicious peer can serve an `InvokeV3` body with non-empty `proof_facts` paired with the canonical hash (computed from the same body with empty `proof_facts`). The node stores the wrong body against the correct hash, creating a permanent hash–body inconsistency in local storage that surfaces as authoritative-looking wrong data through RPC.

---

### Finding Description

**Step 1 — Hash domain is `proof_facts`-conditional.**

`get_invoke_transaction_v3_hash` builds the Poseidon hash chain and appends `proof_facts` only when the field is non-empty:

```rust
if !transaction.proof_facts().0.is_empty() {
    let proof_facts_hash = HashChain::new()
        .chain_iter(transaction.proof_facts().0.iter())
        .get_poseidon_hash();
    hash_chain = hash_chain.chain(&proof_facts_hash);
}
Ok(TransactionHash(hash_chain.get_poseidon_hash()))
``` [1](#0-0) 

This means two `InvokeTransactionV3` values that are identical except one has `proof_facts = []` and the other has `proof_facts = [x, y, z]` produce different hashes H1 and H2 respectively.

**Step 2 — Protobuf deserializer accepts non-empty `proof_facts` without restriction.**

`TryFrom<protobuf::InvokeV3> for InvokeTransactionV3` faithfully decodes the `proof_facts` repeated field from the wire into a `ProofFacts` value with no rejection guard:

```rust
let proof_facts: ProofFacts = value
    .proof_facts
    .into_iter()
    .map(Felt::try_from)
    .collect::<Result<Vec<_>, _>>()?
    .into();
``` [2](#0-1) 

**Step 3 — `TransactionInBlock` deserialization takes hash and body independently with no cross-check.**

`TryFrom<protobuf::TransactionInBlock> for (Transaction, TransactionHash)` extracts `tx_hash` directly from the `transaction_hash` protobuf field and decodes the body separately. The two values are never compared:

```rust
let tx_hash = value
    .transaction_hash
    .clone()
    .ok_or(missing("Transaction::transaction_hash"))?
    .try_into()
    .map(TransactionHash)?;
// ... body decoded independently ...
Ok((transaction, tx_hash))
``` [3](#0-2) 

**Step 4 — P2P sync client stores the peer-supplied hash verbatim with an explicit unresolved TODO.**

```rust
// TODO(eitan): Validate transaction hash from untrusted sources
block_body.transaction_hashes.push(transaction_hash);
``` [4](#0-3) 

The TODO confirms the validation is absent in production code. The hash is written to storage as-is.

**Concrete attack construction:**

Let `T_canonical` be a canonical `InvokeV3` with `proof_facts = []` and canonical hash H1 (committed in the block header's `transaction_commitment`).

A malicious peer constructs a `TransactionInBlock` protobuf:
- `txn` = `InvokeV3` with all fields identical to `T_canonical` **except** `proof_facts = [x, y, z]`
- `transaction_hash` = H1 (the canonical hash, which matches the block header commitment)

The p2p sync client:
1. Decodes the body → `InvokeTransactionV3 { proof_facts: [x,y,z], ... }`
2. Takes `transaction_hash = H1` verbatim
3. Writes both to storage without verification

Result in storage:
- Stored body: `proof_facts = [x, y, z]`
- Stored hash: H1
- `get_invoke_transaction_v3_hash(stored_body)` = H2 ≠ H1

The stored hash H1 still matches the block header's `transaction_commitment` (so header-level checks pass), but the stored body is wrong.

---

### Impact Explanation

Any RPC endpoint that reads the stored transaction body and returns it to callers (e.g., `starknet_getTransactionByHash`, tracing, simulation) will return the crafted `proof_facts` as if they were the canonical transaction fields. This is an authoritative-looking wrong value. Additionally, any component that recomputes the hash from the stored body (e.g., for re-execution, tracing, or serving other p2p peers) will compute H2 ≠ H1, causing hash lookup failures or propagating the corrupted body to downstream nodes.

**Impact category:** High — RPC returns an authoritative-looking wrong value; transaction hash/body binding is broken.

---

### Likelihood Explanation

Any peer that the syncing node connects to can execute this attack. The peer only needs to know the canonical transaction body (publicly available from the chain) and serve it with modified `proof_facts` and the correct canonical hash. No special privileges are required. The attack is silent — the node stores the data without error.

---

### Recommendation

In `parse_data_for_block` in `crates/apollo_p2p_sync/src/client/transaction.rs`, after decoding each `FullTransaction`, recompute the transaction hash from the decoded body and compare it against the peer-supplied `transaction_hash`. If they differ, treat it as a `BadPeerError` and disconnect/report the peer. This resolves the existing TODO and closes the hash–body inconsistency window.

---

### Proof of Concept

```rust
// Demonstrates that proof_facts changes the hash (Step 1)
let chain_id = ChainId::Mainnet;
let version = TransactionVersion::THREE;

let mut tx = make_invoke_v3_tx(/* all fields */);
tx.proof_facts = ProofFacts(vec![]);
let h1 = tx.calculate_transaction_hash(&chain_id, &version).unwrap();

tx.proof_facts = ProofFacts(vec![Felt::from(42u64)]);
let h2 = tx.calculate_transaction_hash(&chain_id, &version).unwrap();

assert_ne!(h1, h2); // confirmed by conditional branch at transaction_hash.rs:399

// Demonstrates that p2p sync stores peer-supplied hash verbatim (Steps 2-4)
// Construct protobuf TransactionInBlock with proof_facts=[42] but transaction_hash=h1
let proto = protobuf::TransactionInBlock {
    txn: Some(protobuf::transaction_in_block::Txn::InvokeV3(
        /* InvokeV3 with proof_facts=[42] */
    )),
    transaction_hash: Some(h1.0.into()), // canonical hash, not h2
};
let (decoded_tx, decoded_hash) = <(Transaction, TransactionHash)>::try_from(proto).unwrap();
assert_eq!(decoded_hash, h1); // peer-supplied hash stored verbatim
// decoded_tx.proof_facts = [42], but h1 was computed from proof_facts=[]
// recomputing: decoded_tx.calculate_transaction_hash(...) == h2 != h1
```

### Citations

**File:** crates/starknet_api/src/transaction_hash.rs (L399-404)
```rust
    if !transaction.proof_facts().0.is_empty() {
        let proof_facts_hash =
            HashChain::new().chain_iter(transaction.proof_facts().0.iter()).get_poseidon_hash();
        hash_chain = hash_chain.chain(&proof_facts_hash);
    }
    Ok(TransactionHash(hash_chain.get_poseidon_hash()))
```

**File:** crates/apollo_protobuf/src/converters/transaction.rs (L137-183)
```rust
        let tx_hash = value
            .transaction_hash
            .clone()
            .ok_or(missing("Transaction::transaction_hash"))?
            .try_into()
            .map(TransactionHash)?;
        let txn = value.txn.ok_or(missing("Transaction::txn"))?;
        let transaction: Transaction = match txn {
            protobuf::transaction_in_block::Txn::DeclareV0(declare_v0) => Transaction::Declare(
                DeclareTransaction::V0(DeclareTransactionV0V1::try_from(declare_v0)?),
            ),
            protobuf::transaction_in_block::Txn::DeclareV1(declare_v1) => Transaction::Declare(
                DeclareTransaction::V1(DeclareTransactionV0V1::try_from(declare_v1)?),
            ),
            protobuf::transaction_in_block::Txn::DeclareV2(declare_v2) => Transaction::Declare(
                DeclareTransaction::V2(DeclareTransactionV2::try_from(declare_v2)?),
            ),
            protobuf::transaction_in_block::Txn::DeclareV3(declare_v3) => Transaction::Declare(
                DeclareTransaction::V3(DeclareTransactionV3::try_from(declare_v3)?),
            ),
            protobuf::transaction_in_block::Txn::Deploy(deploy) => {
                Transaction::Deploy(DeployTransaction::try_from(deploy)?)
            }
            protobuf::transaction_in_block::Txn::DeployAccountV1(deploy_account_v1) => {
                Transaction::DeployAccount(DeployAccountTransaction::V1(
                    DeployAccountTransactionV1::try_from(deploy_account_v1)?,
                ))
            }
            protobuf::transaction_in_block::Txn::DeployAccountV3(deploy_account_v3) => {
                Transaction::DeployAccount(DeployAccountTransaction::V3(
                    DeployAccountTransactionV3::try_from(deploy_account_v3)?,
                ))
            }
            protobuf::transaction_in_block::Txn::InvokeV0(invoke_v0) => Transaction::Invoke(
                InvokeTransaction::V0(InvokeTransactionV0::try_from(invoke_v0)?),
            ),
            protobuf::transaction_in_block::Txn::InvokeV1(invoke_v1) => Transaction::Invoke(
                InvokeTransaction::V1(InvokeTransactionV1::try_from(invoke_v1)?),
            ),
            protobuf::transaction_in_block::Txn::InvokeV3(invoke_v3) => Transaction::Invoke(
                InvokeTransaction::V3(InvokeTransactionV3::try_from(invoke_v3)?),
            ),
            protobuf::transaction_in_block::Txn::L1Handler(l1_handler) => {
                Transaction::L1Handler(L1HandlerTransaction::try_from(l1_handler)?)
            }
        };
        Ok((transaction, tx_hash))
```

**File:** crates/apollo_protobuf/src/converters/transaction.rs (L640-645)
```rust
        let proof_facts: ProofFacts = value
            .proof_facts
            .into_iter()
            .map(Felt::try_from)
            .collect::<Result<Vec<_>, _>>()?
            .into();
```

**File:** crates/apollo_p2p_sync/src/client/transaction.rs (L88-89)
```rust
                // TODO(eitan): Validate transaction hash from untrusted sources
                block_body.transaction_hashes.push(transaction_hash);
```
