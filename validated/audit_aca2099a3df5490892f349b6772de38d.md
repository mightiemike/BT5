Now I have a complete picture of the call path. Let me verify the key TODO comment and the `add_class` vs `add_deprecated_class` distinction.

### Title
Missing Class Body Hash Verification in P2P Sync Allows Malicious Peer to Store Wrong Contract Code Under Arbitrary Class Hash — (`crates/apollo_p2p_sync/src/client/class.rs`)

---

### Summary

The p2p sync client accepts a `protobuf::Class` message from an untrusted peer, extracts the `class_hash` field verbatim, and stores the Cairo0 class body under that peer-supplied key via `add_deprecated_class(class_hash, body)` — without ever verifying that `hash(body) == class_hash`. A single malicious peer can therefore persist a Cairo0 class body under an arbitrary `ClassHash`, causing the sequencer to execute the wrong contract code whenever that hash is looked up.

---

### Finding Description

**Step 1 — Protobuf deserialization (no hash check)**

`TryFrom<protobuf::Class> for (ApiContractClass, ClassHash)` decodes the class body and reads `class_hash` straight from the wire field:

```rust
let class_hash =
    value.class_hash.ok_or(missing("Class::class_hash"))?.try_into().map(ClassHash)?;
Ok((class, class_hash))
```

No hash is computed over `class`; the peer-supplied felt is accepted as-is. [1](#0-0) 

**Step 2 — `parse_data_for_block`: only a set-membership check**

`ClassStreamBuilder::parse_data_for_block` reads the already-stored state diff and verifies only that the peer-supplied `class_hash` appears in `deprecated_declared_classes`:

```rust
ApiContractClass::DeprecatedContractClass(deprecated_contract_class) => (
    deprecated_declared_classes.contains(&class_hash),   // ← only membership check
    deprecated_declared_classes_result
        .insert(class_hash, deprecated_contract_class)
        .is_some(),
),
```

If the hash is present in the state diff the class body is accepted unconditionally. [2](#0-1) 

The state diff itself is synced from the same peer and is **not** verified against the block header's `state_diff_commitment` anywhere in the p2p sync client path — only its total element count is checked against `state_diff_length`. [3](#0-2) 

**Step 3 — `write_to_storage`: peer-supplied hash used as storage key**

The peer-supplied `class_hash` is forwarded directly to `add_deprecated_class` as the storage key:

```rust
for (class_hash, deprecated_class) in self.1 {
    while let Err(err) = class_manager_client
        .add_deprecated_class(class_hash, deprecated_class.clone())
        .await
```

The codebase itself acknowledges the missing check with a TODO immediately above the Cairo1 branch:
```
// TODO(shahak): Verify class hash matches class manager response. report if not.
``` [4](#0-3) 

`add_deprecated_class` in the class manager calls `set_deprecated_class(class_id, class)` which writes the body to the filesystem keyed by `class_id` — no hash recomputation occurs. [5](#0-4) 

**Scope note — Cairo1 classes are not affected by this exact path.** For Cairo1, `add_class(class_body)` is called without passing the peer-supplied hash; the class manager computes the hash internally. The peer-supplied hash is only used for the state-diff membership check, so the storage key is always the computed hash. [6](#0-5) 

---

### Impact Explanation

When execution later calls `get_executable(class_hash=0x1)`, the class manager returns the body that was stored under `0x1` — which is the attacker-supplied body whose real hash is `0x2`. The sequencer executes the wrong contract code for every contract deployed to that class. This matches the allowed Critical impact: **wrong compiled class or contract code selected for execution when the class is looked up by hash**.

---

### Likelihood Explanation

Any node that syncs classes over p2p from an untrusted peer is exposed. The attacker needs only to:
1. Serve a state diff containing the target `class_hash` (no commitment verification blocks this).
2. Serve a `ClassesResponse` with that `class_hash` but a different Cairo0 body.

No operator privilege, key material, or special network position is required beyond being a connectable p2p peer.

---

### Recommendation

After receiving `(api_contract_class, class_hash)` from the peer, compute the canonical hash of the class body and compare it to the peer-supplied `class_hash` before inserting into storage. For Cairo0:

```rust
let computed = compute_deprecated_class_hash(&deprecated_contract_class)?;
if computed != class_hash.0 {
    return Err(ParseDataError::BadPeer(BadPeerError::ClassHashMismatch { class_hash }));
}
```

For Cairo1, verify that the `ClassHashes.class_hash` returned by `add_class` equals the peer-supplied `class_hash` (the existing TODO at line 39 already tracks this).

---

### Proof of Concept

1. Construct a valid Cairo0 `ContractClass` body `B` whose canonical hash is `0x2`.
2. Serialize it into a `protobuf::Cairo0Class`.
3. Wrap it in a `protobuf::Class { class_hash: felt(0x1), class: Cairo0(B) }`.
4. Serve a state diff containing `deprecated_declared_classes: [0x1]` (length matches header).
5. Serve the `ClassesResponse` containing the above `Class`.
6. After sync completes, call `get_executable(ClassHash(0x1))` on the class manager — it returns `B` (real hash `0x2`).
7. Assert that `compute_deprecated_class_hash(B) != ClassHash(0x1)` — confirming the DB key diverges from the body hash.

### Citations

**File:** crates/apollo_protobuf/src/converters/class.rs (L75-78)
```rust
        let class_hash =
            value.class_hash.ok_or(missing("Class::class_hash"))?.try_into().map(ClassHash)?;
        Ok((class, class_hash))
    }
```

**File:** crates/apollo_p2p_sync/src/client/class.rs (L35-55)
```rust
            for (class_hash, class) in self.0 {
                // We can't continue without writing to the class manager, so we'll keep retrying
                // until it succeeds.
                // TODO(shahak): Test this flow.
                // TODO(shahak): Verify class hash matches class manager response. report if not.
                // TODO(shahak): Try to avoid cloning. See if ClientError can contain the request.
                while let Err(err) = class_manager_client.add_class(class.clone()).await {
                    warn!(
                        "Failed writing class with hash {class_hash:?} to class manager. Trying \
                         again. Error: {err:?}"
                    );
                    trace!("Class: {class:?}");
                    // TODO(shahak): Consider sleeping here.
                }
            }

            for (class_hash, deprecated_class) in self.1 {
                // TODO(shahak): Test this flow.
                // TODO(shahak): Try to avoid cloning. See if ClientError can contain the request.
                while let Err(err) = class_manager_client
                    .add_deprecated_class(class_hash, deprecated_class.clone())
```

**File:** crates/apollo_p2p_sync/src/client/class.rs (L136-141)
```rust
                    ApiContractClass::DeprecatedContractClass(deprecated_contract_class) => (
                        deprecated_declared_classes.contains(&class_hash),
                        deprecated_declared_classes_result
                            .insert(class_hash, deprecated_contract_class)
                            .is_some(),
                    ),
```

**File:** crates/apollo_p2p_sync/src/client/state_diff.rs (L62-70)
```rust
            let target_state_diff_len = storage_reader
                .begin_ro_txn()?
                .get_block_header(block_number)?
                .expect("A header with number lower than the header marker is missing")
                .state_diff_length
                .ok_or(P2pSyncClientError::OldHeaderInStorage {
                    block_number,
                    missing_field: "state_diff_length",
                })?;
```

**File:** crates/apollo_class_manager/src/class_storage.rs (L199-215)
```rust
    fn set_deprecated_class(
        &mut self,
        class_id: ClassId,
        class: RawExecutableClass,
    ) -> Result<(), Self::Error> {
        if self.deprecated_class_cached(class_id) {
            return Ok(());
        }

        self.storage.set_deprecated_class(class_id, class.clone())?;

        increment_n_classes(CairoClassType::Deprecated);
        record_class_size(ClassObjectType::DeprecatedCasm, &class);

        self.deprecated_classes.set(class_id, class);

        Ok(())
```
