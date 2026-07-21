### Title
Unauthenticated `ProofManager::set_proof` gRPC Endpoint Allows Proof-Verification Bypass in `run_proof_verification` - (File: `crates/apollo_proof_manager/src/proof_manager.rs`, `crates/apollo_transaction_converter/src/transaction_converter.rs`)

---

### Summary

The `ProofManager` exposes a `set_proof` gRPC endpoint with no authentication or caller verification. The `TransactionConverter::run_proof_verification` function skips cryptographic proof verification whenever `contains_proof` returns true, trusting that any stored proof was previously verified. An attacker with network access to the proof manager's gRPC port can call `set_proof` directly with arbitrary `proof_facts` and a fake proof, pre-populating the store. When a transaction carrying those `proof_facts` is subsequently submitted to the gateway, `run_proof_verification` skips verification entirely and the transaction is admitted to the mempool without a valid cryptographic proof.

---

### Finding Description

**Invariant broken**: `run_proof_verification` assumes that any proof already present in the proof manager was stored only after successful cryptographic verification. This assumption is violated because `ProofManager::set_proof` is an unauthenticated endpoint.

**Root cause — `ProofManager::set_proof` stores without verifying:** [1](#0-0) 

The function accepts any `(ProofFacts, Proof)` pair from any caller and writes it to persistent storage and the LRU cache without invoking `starknet_proof_verifier::verify_proof`.

**Root cause — `run_proof_verification` skips verification on cache hit:** [2](#0-1) 

When `contains_proof` returns `true`, the function returns `Ok(false)` immediately, bypassing the `spawn_blocking` call to `starknet_proof_verifier::verify_proof`.

**Exposure — proof manager gRPC server bound to `0.0.0.0` with no authentication:** [3](#0-2) 

The `RemoteProofManagerServer` is a plain gRPC server with no TLS, no token, and no caller identity check. Any network-reachable peer can issue `SetProof` requests.

**Gateway flow — verification task is spawned and awaited before mempool admission:** [4](#0-3) 

The gateway calls `spawn_proof_verification`, which internally calls `run_proof_verification`. If the attacker has pre-populated the store, this task returns `Ok(false)` (skipped), and the gateway proceeds to admit the transaction.

**Consensus flow — same skip applies:** [5](#0-4) 

`spawn_verify_and_store_proof` also calls `run_proof_verification` and skips verification on a cache hit, so the same bypass applies during proposal validation.

---

### Impact Explanation

**High — Mempool/gateway admission accepts invalid transactions.**

A transaction carrying attacker-chosen `proof_facts` (structurally valid: correct block hash, program hash, config hash) but no real cryptographic proof is admitted to the mempool. The blockifier's `perform_pre_validation_stage` validates the *content* of `proof_facts` (block number recency, block hash, program hash, config hash) but does **not** re-run the circuit verifier — that check is exclusively the gateway/consensus responsibility. Because the gateway skipped it, the transaction passes all blockifier pre-validation checks and is executed.

The client-side proving feature is therefore bypassed: a transaction can claim the execution benefits of a proven block without possessing a valid STARK proof.

---

### Likelihood Explanation

In a distributed deployment the proof manager runs as a standalone pod with its gRPC port bound to `0.0.0.0`. Any pod in the same Kubernetes namespace (or any host with a route to that port) can issue unauthenticated `SetProof` RPCs. No special privilege is required beyond network reachability. The attacker also controls a Starknet account (needed to sign the transaction), which is an unprivileged precondition.

---

### Recommendation

1. **Add caller authentication to the proof manager gRPC server.** Use mutual TLS or a shared secret so that only the gateway and consensus components can call `SetProof`.
2. **Verify before storing.** Move the `starknet_proof_verifier::verify_proof` call into `ProofManager::set_proof` itself so that no unverified proof can ever enter the store, regardless of which caller invokes the endpoint.
3. **Remove the `contains_proof` short-circuit in `run_proof_verification`**, or at minimum ensure the short-circuit is only reachable after the proof has been cryptographically verified by the same process.

---

### Proof of Concept

```
# 1. Attacker crafts structurally valid proof_facts (correct block hash, program hash, config hash)
#    for a recent committed block B.

# 2. Attacker calls SetProof on the unauthenticated proof manager gRPC endpoint:
grpcurl -plaintext <proof_manager_host>:<port> \
  ProofManagerRequest/SetProof \
  '{"proof_facts": <valid_proof_facts_felts>, "proof": <empty_or_garbage_bytes>}'

# 3. Proof manager stores the fake proof at proof_facts.hash() without verification.

# 4. Attacker submits an invoke V3 transaction to the gateway with:
#    - proof_facts = <same valid_proof_facts>
#    - proof = <empty_or_garbage_bytes>
#    - valid ECDSA signature over the transaction hash (attacker controls the account)

# 5. Gateway calls run_proof_verification:
#    contains_proof(proof_facts) → true  (attacker pre-populated)
#    → returns Ok(false), skipping starknet_proof_verifier::verify_proof

# 6. Transaction is admitted to the mempool and forwarded to the batcher.
#    Blockifier validates proof_facts content (block hash, program hash, config hash) → passes.
#    Transaction executes with client-side proving semantics, no valid proof ever verified.
```

### Citations

**File:** crates/apollo_proof_manager/src/proof_manager.rs (L54-66)
```rust
    pub async fn set_proof(
        &self,
        proof_facts: ProofFacts,
        proof: Proof,
    ) -> Result<(), FsProofStorageError> {
        if self.contains_proof(proof_facts.clone()).await? {
            return Ok(());
        }
        let facts_hash = proof_facts.hash();
        self.proof_storage.set_proof(facts_hash, proof.clone()).await?;
        self.cache.insert(facts_hash, proof);
        Ok(())
    }
```

**File:** crates/apollo_transaction_converter/src/transaction_converter.rs (L256-265)
```rust
    async fn convert_rpc_tx_to_internal_rpc_tx(
        &self,
        tx: RpcTransaction,
    ) -> TransactionConverterResult<(InternalRpcTransaction, Option<VerificationHandle>)> {
        let (internal_tx, proof_data) = self.convert_rpc_tx_to_internal(tx).await?;
        let verification_handle = proof_data
            .map(|(proof_facts, proof)| self.spawn_proof_verification(proof_facts, proof))
            .transpose()?;
        Ok((internal_tx, verification_handle))
    }
```

**File:** crates/apollo_transaction_converter/src/transaction_converter.rs (L398-407)
```rust
    async fn run_proof_verification(
        proof_facts: ProofFacts,
        proof: Proof,
        proof_manager_client: SharedProofManagerClient,
    ) -> Result<bool, TransactionConverterError> {
        let contains_proof = proof_manager_client.contains_proof(proof_facts.clone()).await?;

        if contains_proof {
            return Ok(false);
        }
```

**File:** crates/apollo_transaction_converter/src/transaction_converter.rs (L446-471)
```rust
    fn spawn_verify_and_store_proof(
        &self,
        proof_facts: ProofFacts,
        proof: Proof,
    ) -> VerifyAndStoreProofTask {
        let pmc = self.proof_manager_client.clone();
        let proof_facts_hash = proof_facts.hash();
        tokio::spawn(async move {
            let verified =
                Self::run_proof_verification(proof_facts.clone(), proof.clone(), pmc.clone())
                    .await?;

            if !verified {
                return Ok(());
            }

            let start = Instant::now();
            pmc.set_proof(proof_facts, proof).await?;
            let duration = start.elapsed();
            CONSENSUS_PROOF_MANAGER_STORE_LATENCY.record(duration.as_secs_f64());
            info!(
                "Proof manager store took: {duration:?} for proof facts hash: {proof_facts_hash:?}"
            );
            Ok(())
        })
    }
```

**File:** crates/apollo_deployments/resources/services/distributed/proof_manager.json (L87-91)
```json
  "components.proof_manager.remote_server_config.#is_none": false,
  "components.proof_manager.remote_server_config.bind_ip": "0.0.0.0",
  "components.proof_manager.remote_server_config.max_streams_per_connection": 8,
  "components.proof_manager.remote_server_config.set_tcp_nodelay": true,
  "components.proof_manager.url": "remote_service",
```
