The key facts are:

1. **Local topic constant** in `crates/apollo_mempool_p2p/src/lib.rs` line 41:
   ```
   "apollo_mempool_transaction_propagation/0.1.0"
   ```

2. **Spec-mandated topic** in `crates/apollo_protobuf/src/proto/p2p/proto/mempool/mempool.md` line 10:
   ```
   "/starknet/mempool_transaction_propagation/0.1.0"
   ```

3. **Production `Topic` type** in `crates/apollo_network/src/gossipsub_impl.rs` lines 34–35:
   ```rust
   #[cfg(not(test))]
   pub type Topic = gossipsub::Sha256Topic;
   ```

These are two distinct byte strings. `Sha256Topic::new(s).hash()` computes `SHA256(s)`, so the two topic names produce different `TopicHash` values. The Apollo node subscribes to the wrong hash in production, making it invisible to any spec-compliant peer and vice versa.

---

### Title
Topic Name Mismatch Isolates Apollo Mempool from Spec-Compliant Peers — (`crates/apollo_mempool_p2p/src/lib.rs`)

### Summary
`MEMPOOL_TOPIC` is set to `"apollo_mempool_transaction_propagation/0.1.0"` while the Starknet p2p spec mandates `"/starknet/mempool_transaction_propagation/0.1.0"`. In production, `gossipsub::Sha256Topic` hashes these two strings to different `TopicHash` values, so the Apollo node and any spec-compliant peer subscribe to disjoint gossipsub meshes. No transactions flow between them.

### Finding Description
`MEMPOOL_TOPIC` is defined as: [1](#0-0) 

The protocol specification states the canonical topic is `"/starknet/mempool_transaction_propagation/0.1.0"`: [2](#0-1) 

In production builds, `Topic` resolves to `gossipsub::Sha256Topic`: [3](#0-2) 

`Topic::new(MEMPOOL_TOPIC)` is passed directly to `register_broadcast_topic` and used for the metrics key: [4](#0-3) [5](#0-4) 

Because `SHA256("apollo_mempool_transaction_propagation/0.1.0") ≠ SHA256("/starknet/mempool_transaction_propagation/0.1.0")`, the Apollo node's gossipsub subscription is on a completely different topic hash than any peer following the spec.

In test builds, `Topic = gossipsub::IdentTopic`, which uses the raw string as the hash, so the mismatch is masked in all unit/integration tests. [6](#0-5) 

### Impact Explanation
Every transaction broadcast by a spec-compliant peer on `"/starknet/mempool_transaction_propagation/0.1.0"` is silently dropped by the Apollo node because the `TopicHash` does not match any registered subscription. Conversely, transactions the Apollo node broadcasts are never received by spec-compliant peers. The result is complete mempool p2p isolation: valid transactions submitted to spec-compliant nodes never reach the Apollo sequencer for admission, satisfying the **High** impact criterion — mempool admission rejects valid transactions before sequencing.

### Likelihood Explanation
The divergence is unconditional in any production binary (non-test build). Any spec-compliant peer connecting to the Apollo node will trigger this silently. No special attacker capability is required; ordinary network participation suffices.

### Recommendation
Change `MEMPOOL_TOPIC` to match the spec:

```rust
pub const MEMPOOL_TOPIC: &str = "/starknet/mempool_transaction_propagation/0.1.0";
```

Add a non-`#[cfg(test)]` compile-time or startup assertion that `Topic::new(MEMPOOL_TOPIC).hash()` equals the expected canonical hash to prevent future regressions.

### Proof of Concept
```rust
use libp2p::gossipsub::Sha256Topic;

let apollo_hash  = Sha256Topic::new("apollo_mempool_transaction_propagation/0.1.0").hash();
let spec_hash    = Sha256Topic::new("/starknet/mempool_transaction_propagation/0.1.0").hash();
assert_eq!(apollo_hash, spec_hash); // FAILS — divergent TopicHash values
```

### Citations

**File:** crates/apollo_mempool_p2p/src/lib.rs (L41-41)
```rust
pub const MEMPOOL_TOPIC: &str = "apollo_mempool_transaction_propagation/0.1.0";
```

**File:** crates/apollo_mempool_p2p/src/lib.rs (L57-57)
```rust
        Topic::new(MEMPOOL_TOPIC).hash(),
```

**File:** crates/apollo_mempool_p2p/src/lib.rs (L87-93)
```rust
    let BroadcastTopicChannels { broadcasted_messages_receiver, broadcast_topic_client } =
        network_manager
            .register_broadcast_topic(
                Topic::new(MEMPOOL_TOPIC),
                mempool_p2p_config.network_buffer_size,
            )
            .expect("Failed to register broadcast topic");
```

**File:** crates/apollo_protobuf/src/proto/p2p/proto/mempool/mempool.md (L10-10)
```markdown
The topic for this protocol is: `"/starknet/mempool_transaction_propagation/0.1.0"`.
```

**File:** crates/apollo_network/src/gossipsub_impl.rs (L31-32)
```rust
#[cfg(test)]
pub type Topic = gossipsub::IdentTopic;
```

**File:** crates/apollo_network/src/gossipsub_impl.rs (L34-35)
```rust
#[cfg(not(test))]
pub type Topic = gossipsub::Sha256Topic;
```
