### Title
Shared Proof-Manager Cache Allows Proof Verification Bypass via Proof-Facts Reuse — (`crates/apollo_transaction_converter/src/transaction_converter.rs`)

### Summary

The `ProofManager` is a shared, node-wide service keyed solely by `proof_facts.hash()`. `run_proof_verification` skips cryptographic proof verification entirely when `contains_proof(proof_facts)` returns `true`. Because `proof_facts` are public (visible in any accepted invoke V3 transaction) and are not bound to a specific sender, nonce, or proof bytes, any unprivileged user can copy another user's `proof_facts`, attach arbitrary (invalid) proof bytes, and have the gateway accept the transaction without ever verifying the proof. This is the direct sequencer analog of the `OpportunityAdapter` shared-allowance exploit: the shared resource (proof manager cache) is exploited by one user to bypass a check that was only satisfied by another user.

### Finding Description

`run_proof_verification` in `crates/apollo_transaction_converter/src/transaction_converter.rs` implements the following logic:

```rust
async fn run_proof_verification(
    proof_facts: ProofFacts,
    proof: Proof,
    proof_manager_client: SharedProofManagerClient,
) -> Result<bool, TransactionConverterError> {
    let contains_proof = proof_manager_client.contains_proof(proof_facts.clone()).await?;

    if contains_proof {
        return Ok(false);   // ← proof bytes are NEVER examined
    }
    // ... cryptographic verification only reaches here on first submission
    tokio::task::spawn_blocking(move || {
        starknet_proof_verifier::verify_proof(proof_facts, proof)
    })
    ...
}
``` [1](#0-0) 

The `ProofManager` stores proofs keyed by `proof_facts.hash()`:

```rust
pub fn hash(&self) -> Felt {
    HashChain::new().chain_iter(self.0.iter()).get_poseidon_hash()
}
``` [2](#0-1) 

The key is derived exclusively from the `proof_facts` content — the submitted `proof` bytes play no role in the cache lookup. Once any transaction with `proof_facts = F` has been verified and stored, every subsequent transaction carrying the same `F` bypasses `verify_proof` entirely, regardless of what proof bytes are submitted.

The stateless validator enforces only that `proof_facts` and `proof` are both non-empty or both empty (`validate_proof_facts_and_proof_consistency`): [3](#0-2) 

It does not verify the proof bytes. Cryptographic verification is deferred entirely to `run_proof_verification`, which is where the bypass occurs.

The gateway awaits the verification handle and propagates errors — but `Ok(false)` (skipped) is treated identically to `Ok(true)` (verified): [4](#0-3) 

The same `run_proof_verification` path is shared by both the gateway flow (`spawn_proof_verification`) and the consensus flow (`spawn_verify_and_store_proof`): [5](#0-4) 

### Impact Explanation

**Impact: High — Mempool/gateway/RPC admission accepts invalid transactions before sequencing.**

The gateway accepts an invoke V3 transaction whose `proof` field contains arbitrary (cryptographically invalid) bytes, provided the `proof_facts` field matches any previously verified transaction. The invariant "every accepted transaction with non-empty `proof_facts` must have its proof cryptographically verified" is broken. The accepted transaction carries `proof_facts` that are part of its transaction hash and signature domain, and the OS validates the `proof_facts` content (block number, block hash, config hash, program hash) during execution — but the cryptographic proof itself is never checked for the attacker's transaction.

### Likelihood Explanation

**Likelihood: High.**

- `proof_facts` are public: they appear in the `RpcInvokeTransactionV3` struct, are broadcast over P2P, and are visible in any accepted transaction.
- No privileged access is required. Any account can submit an invoke V3 transaction.
- The `allow_client_side_proving` flag is `true` by default in production config.
- The attacker only needs to observe one previously accepted transaction with non-empty `proof_facts`, copy those felts verbatim, and attach any non-empty byte sequence as the proof.
- The attack is repeatable for every distinct `proof_facts` value that has ever been stored.

### Recommendation

Bind proof verification to the submitted proof bytes, not only to the proof_facts key. Options:

1. **Do not skip verification based on cache presence alone.** Always call `verify_proof(proof_facts, proof)`. Use the cache only to avoid redundant *storage* writes, not to skip cryptographic verification.
2. **Key the cache on `(proof_facts.hash(), proof_hash)`.** If the same `(proof_facts, proof)` pair was already verified, skip re-verification. A different proof for the same facts must still be verified.
3. **Verify the proof bytes before the cache lookup.** Run `verify_proof` unconditionally; only skip the subsequent `set_proof` write if the entry already exists.

### Proof of Concept

1. **Setup**: User A submits a valid invoke V3 transaction with `proof_facts = F` and valid proof `P`. The gateway calls `run_proof_verification(F, P, ...)` → `contains_proof(F)` = `false` → `verify_proof(F, P)` succeeds → proof stored under `F.hash()`.

2. **Attack**: Attacker observes `F` (e.g., from the mempool or P2P broadcast). Attacker constructs their own invoke V3 transaction with:
   - `sender_address` = attacker's address
   - `nonce` = attacker's current nonce
   - `proof_facts` = `F` (copied from User A)
   - `proof` = `[0x01]` (one arbitrary byte — satisfies `has_proof == has_proof_facts`)
   - Valid ECDSA signature over the transaction hash (which includes `F`)

3. **Gateway processing**: Stateless validator passes (`proof_facts` and `proof` are both non-empty). `convert_rpc_tx_to_internal_rpc_tx` extracts `proof_data = Some((F, [0x01]))`. `spawn_proof_verification(F, [0x01])` is called. Inside `run_proof_verification`: `contains_proof(F)` = `true` → returns `Ok(false)` → **`verify_proof` is never called**.

4. **Result**: The gateway accepts the attacker's transaction with an invalid, unverified proof and forwards it to the mempool. The broken invariant: `proof` bytes `[0x01]` are accepted as if they were a valid cryptographic proof for `proof_facts = F`. [6](#0-5) [7](#0-6) [3](#0-2)

### Citations

**File:** crates/apollo_transaction_converter/src/transaction_converter.rs (L398-424)
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

        let proof_facts_hash = proof_facts.hash();
        let verify_start = Instant::now();
        tokio::task::spawn_blocking(move || {
            starknet_proof_verifier::verify_proof(proof_facts, proof)
        })
        .await
        .expect("proof verification task panicked")?;
        let verify_duration = verify_start.elapsed();
        PROOF_VERIFICATION_LATENCY.record(verify_duration.as_secs_f64());
        info!(
            "Proof verification took: {verify_duration:?} for proof facts hash: \
             {proof_facts_hash:?}"
        );

        Ok(true)
    }
```

**File:** crates/apollo_transaction_converter/src/transaction_converter.rs (L426-471)
```rust
    /// Spawns a verification-only task. Used by the gateway flow, which stores the proof
    /// separately after all validations pass.
    fn spawn_proof_verification(
        &self,
        proof_facts: ProofFacts,
        proof: Proof,
    ) -> TransactionConverterResult<VerificationHandle> {
        let pmc = self.proof_manager_client.clone();
        let task_proof_facts = proof_facts.clone();
        let task_proof = proof.clone();
        let verification_task = tokio::spawn(async move {
            Self::run_proof_verification(task_proof_facts, task_proof, pmc).await?;
            Ok(())
        });
        Ok(VerificationHandle { proof_facts, proof, verification_task })
    }

    /// Spawns a single task that verifies the proof and then stores it in the proof manager.
    /// Used by the consensus flow, where tasks run concurrently with batcher execution and
    /// are awaited at fin.
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

**File:** crates/starknet_api/src/transaction/fields.rs (L709-711)
```rust
    pub fn hash(&self) -> Felt {
        HashChain::new().chain_iter(self.0.iter()).get_poseidon_hash()
    }
```

**File:** crates/apollo_gateway/src/stateless_transaction_validator.rs (L249-263)
```rust
    fn validate_proof_facts_and_proof_consistency(
        &self,
        tx: &RpcInvokeTransaction,
    ) -> StatelessTransactionValidatorResult<()> {
        let RpcInvokeTransaction::V3(tx) = tx;
        let has_proof_facts = !tx.proof_facts.is_empty();
        let has_proof = !tx.proof.is_empty();
        if has_proof_facts != has_proof {
            return Err(StatelessTransactionValidatorError::ProofFactsAndProofConsistency {
                has_proof_facts,
                has_proof,
            });
        }
        Ok(())
    }
```

**File:** crates/apollo_gateway/src/gateway.rs (L467-489)
```rust
    async fn await_verification_task_and_extract_proof_data(
        &self,
        verification_handle: Option<VerificationHandle>,
        tx_signature: &TransactionSignature,
    ) -> Result<Option<(ProofFacts, Proof)>, StarknetError> {
        let Some(handle) = verification_handle else {
            return Ok(None);
        };

        handle
            .verification_task
            .await
            .map_err(|e| {
                warn!("Proof verification task panicked: {}", e);
                StarknetError::internal_with_logging("Proof verification task panicked:", &e)
            })?
            .map_err(|e| {
                warn!("Proof verification failed: {}", e);
                transaction_converter_err_to_deprecated_gw_err(tx_signature, e)
            })?;

        Ok(Some((handle.proof_facts, handle.proof)))
    }
```

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
