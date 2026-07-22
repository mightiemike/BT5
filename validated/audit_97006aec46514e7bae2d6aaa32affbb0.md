### Title
`verify_proof` in gateway/consensus admission does not check `program_hash` against the trusted `allowed_virtual_os_program_hashes` allowlist — (`crates/starknet_proof_verifier/src/proof_verifier.rs`)

---

### Summary

The sequencer's proof-admission path (`run_proof_verification`) calls `starknet_proof_verifier::verify_proof`, which performs only cryptographic circuit verification. It never checks that the `program_hash` embedded in the proof facts belongs to the operator-trusted `allowed_virtual_os_program_hashes` list maintained in `VersionedConstants`. The blockifier's `validate_proof_facts` does enforce this allowlist, but only at execution time — after the transaction has already been admitted to the mempool and the proof has been stored in the proof manager. A submitter who possesses a valid proof for an unauthorized virtual-OS program can therefore pass gateway and consensus admission unconditionally.

---

### Finding Description

**Proof-facts layout** (from `reconstruct_output_preimage`):

```
proof_facts = [PROOF_VERSION_V*, VIRTUAL_SNOS, program_hash, output_version,
               block_number, block_hash, config_hash, ...]
```

`verify_proof` skips indices 0–1 (version + variant marker) and feeds the remainder — including `program_hash` at index 2 — into the recursive circuit verifier as the `output_preimage`. The circuit verifier confirms that the supplied proof is cryptographically consistent with that preimage, but it has no concept of an "allowed program hash list". The check is purely mathematical. [1](#0-0) 

The gateway and consensus converter both reach `run_proof_verification`, which calls `verify_proof` and returns success without ever consulting `allowed_virtual_os_program_hashes`: [2](#0-1) 

The allowlist check exists only in the blockifier's `validate_proof_facts`, which runs during `perform_pre_validation_stage` — well after gateway admission: [3](#0-2) 

The trusted list is version-gated in `VersionedConstants` / `OsConstants`: [4](#0-3) 

**Divergence table**

| Layer | `program_hash` allowlist check | `config_hash` check |
|---|---|---|
| `verify_proof` (gateway/consensus) | **absent** | **absent** |
| `validate_proof_facts` (blockifier) | present | present |

---

### Impact Explanation

**High — Mempool/gateway/RPC admission accepts invalid transactions.**

An attacker who controls an unauthorized virtual-OS binary (e.g., one that omits critical execution constraints) and can generate a cryptographically valid proof for it can:

1. Embed the unauthorized `program_hash` in proof facts.
2. Submit an Invoke V3 transaction with that proof to the gateway or via consensus.
3. `run_proof_verification` calls `verify_proof`, which passes (the proof is cryptographically valid for the unauthorized program).
4. The proof is stored in the proof manager; the transaction enters the mempool.
5. The blockifier later rejects the transaction at pre-validation — but the gateway has already accepted it, the proof manager has stored it, and sequencer resources have been consumed.

The direct consequence is that the gateway/consensus admission boundary is weaker than the blockifier boundary: transactions that the blockifier will always reject can be injected into the mempool at will, bypassing the intended allowlist gate.

---

### Likelihood Explanation

**Low-to-medium.** The attacker must possess a valid proof for an unauthorized virtual-OS program, which requires running a modified Cairo VM and a working Stwo prover. This is a non-trivial capability. However, the barrier is identical in nature to the external bug (constructing an unauthorized recursion vk tree), and the missing check is a single-line omission in a security-critical path.

---

### Recommendation

Add an explicit `program_hash` allowlist check inside `run_proof_verification` (or in `verify_proof` itself, if the allowed set is passed as a parameter) before the cryptographic verification step, mirroring the check already present in `validate_proof_facts`:

```rust
// In run_proof_verification, after parsing proof_facts:
let snos = SnosProofFacts::try_from(proof_facts.clone())
    .map_err(|e| TransactionConverterError::ProofVerificationError(...))?;
let allowed = versioned_constants.os_constants.allowed_virtual_os_program_hashes;
if !allowed.contains(&snos.program_hash) {
    return Err(TransactionConverterError::ProofVerificationError(
        VerifyProofError::UnauthorizedProgramHash { hash: snos.program_hash }
    ));
}
```

The `config_hash` should be validated against the node's own `virtual_os_config_hash` at the same point, for the same reason.

---

### Proof of Concept

1. Compile a modified virtual-OS Cairo program that omits a constraint (e.g., skips the `starknet_os_config_hash` assertion).
2. Compute its `program_hash` via `compute_program_hash_blake`.
3. Run the modified program over a historical block to obtain a Cairo PIE; prove it with Stwo to get a valid `Proof`.
4. Construct `ProofFacts` with `[PROOF_VERSION_V1, VIRTUAL_SNOS, <unauthorized_program_hash>, ...]`.
5. Submit an Invoke V3 transaction carrying these `proof_facts` and `proof` to the gateway.
6. Observe: `run_proof_verification` → `verify_proof` returns `Ok(())` (cryptographic check passes); the proof is stored; the transaction enters the mempool.
7. Observe: the blockifier's `validate_proof_facts` rejects the transaction with `"Virtual OS program hash … is not allowed"` — but admission has already succeeded. [5](#0-4) [6](#0-5) [7](#0-6)

### Citations

**File:** crates/starknet_proof_verifier/src/proof_verifier.rs (L108-121)
```rust
pub fn reconstruct_output_preimage(
    proof_facts: &ProofFacts,
) -> Result<Vec<Felt>, VerifyProofError> {
    // Proof facts must contain at least [PROOF_VERSION_V*, variant, program_hash].
    if proof_facts.0.len() < 3 {
        return Err(VerifyProofError::ProofFactsTooShort { length: proof_facts.0.len() });
    }
    // Skip PROOF_VERSION_V* (index 0) and variant (index 1).
    let task_content = &proof_facts.0[2..];
    let output_size = Felt::from(
        u64::try_from(task_content.len() + 1).expect("task content length exceeds u64::MAX"),
    );
    Ok([Felt::ONE, output_size].into_iter().chain(task_content.iter().copied()).collect())
}
```

**File:** crates/starknet_proof_verifier/src/proof_verifier.rs (L126-157)
```rust
pub fn verify_proof(proof_facts: ProofFacts, proof: Proof) -> Result<(), VerifyProofError> {
    // Reject empty proof payloads before running the verifier.
    if proof.is_empty() {
        return Err(VerifyProofError::EmptyProof);
    }

    let proof_version_felt = proof_facts.0.first().copied().unwrap_or_default();
    let proof_version = ProofVersion::try_from(proof_version_felt)
        .map_err(|()| VerifyProofError::InvalidProofVersion { actual: proof_version_felt })?;

    let output_preimage = reconstruct_output_preimage(&proof_facts)?;
    // TODO(Avi): Avoid cloning the proof.
    let proof_bytes = proof.0.to_vec();

    match proof_version {
        // V0 proofs are no longer verifiable: the v0 circuit was removed. V0 proof facts are only
        // tolerated by the blockifier (gated per protocol version) for replaying historical blocks.
        ProofVersion::V0 => {
            return Err(VerifyProofError::InvalidProofVersion { actual: proof_version_felt });
        }
        ProofVersion::V1 => {
            let proof_output = privacy_circuit_verify_v1::PrivacyProofOutput {
                proof: proof_bytes,
                output_preimage,
            };
            privacy_circuit_verify_v1::verify_recursive_circuit(&proof_output)
                .map_err(|e| VerifyProofError::Verification(e.to_string()))?;
        }
    }

    Ok(())
}
```

**File:** crates/apollo_transaction_converter/src/transaction_converter.rs (L395-424)
```rust
    /// Runs proof verification: checks if the proof already exists, and if not, verifies it.
    /// Returns `true` if verification was performed, `false` if skipped (proof already stored).
    /// This is the shared verification logic used by both gateway and consensus flows.
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

**File:** crates/blockifier/src/transaction/account_transaction.rs (L291-351)
```rust
    fn validate_proof_facts(
        &self,
        block_context: &BlockContext,
        state: &mut dyn State,
    ) -> TransactionPreValidationResult<()> {
        // Only Invoke V3 transactions can carry proof facts.
        let Transaction::Invoke(invoke_tx) = &self.tx else {
            return Ok(());
        };
        if invoke_tx.version() < TransactionVersion::THREE {
            return Ok(());
        }

        // Parse proof facts.
        let proof_facts = invoke_tx.proof_facts();
        let snos_proof_facts = match ProofFactsVariant::try_from(&proof_facts)
            .map_err(|e| TransactionPreValidationError::InvalidProofFacts(e.to_string()))?
        {
            ProofFactsVariant::Empty => return Ok(()),
            ProofFactsVariant::Snos(snos_proof_facts) => snos_proof_facts,
        };
        let os_constants = &block_context.versioned_constants.os_constants;

        if !os_constants.allowed_proof_versions.contains(&snos_proof_facts.proof_version.as_felt())
        {
            return Err(TransactionPreValidationError::InvalidProofFacts(format!(
                "Proof version {} is not allowed under this protocol version.",
                snos_proof_facts.proof_version
            )));
        }

        // Validate the program hash.
        let allowed = &os_constants.allowed_virtual_os_program_hashes;
        if !allowed.contains(&snos_proof_facts.program_hash) {
            return Err(TransactionPreValidationError::InvalidProofFacts(format!(
                "Virtual OS program hash {} is not allowed",
                snos_proof_facts.program_hash
            )));
        }

        // Validate the block hash and block number.
        let proof_block_hash = snos_proof_facts.block_hash.0;
        let proof_block_number = snos_proof_facts.block_number.0;
        Self::validate_proof_block_number(
            proof_block_number,
            block_context.block_info.block_number,
        )?;
        Self::validate_proof_block_hash(proof_block_hash, proof_block_number, os_constants, state)?;

        // Validate the config hash.
        let virtual_os_config_hash = block_context.virtual_os_config_hash();
        let proof_config_hash = snos_proof_facts.config_hash;
        if virtual_os_config_hash != proof_config_hash {
            return Err(TransactionPreValidationError::InvalidProofFacts(format!(
                "Virtual OS config hash mismatch. Computed virtual OS config hash: \
                 {virtual_os_config_hash}, expected virtual OS config hash: {proof_config_hash}."
            )));
        }

        Ok(())
    }
```

**File:** crates/blockifier/resources/blockifier_versioned_constants_0_14_2.json (L129-131)
```json
        "allowed_virtual_os_program_hashes": [
            "0x3e98c2d7703b03a7edb73ed7f075f97f1dcbaa8f717cdf6e1a57bf058265473"
        ],
```
