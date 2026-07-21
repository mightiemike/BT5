### Title
`StatelessTransactionValidator::validate()` Never Calls `validate_proof_size()`, Allowing Oversized-Proof Invoke Transactions Through Gateway Admission - (File: crates/apollo_gateway/src/stateless_transaction_validator.rs)

### Summary

`StatelessTransactionValidator::validate()` applies several invoke-specific checks when it encounters an `RpcTransaction::Invoke`, but silently omits the call to `validate_proof_size()`. The method is fully implemented and wired to a `max_proof_size` config field, yet it is never invoked. Any invoke transaction carrying a proof whose element count exceeds `max_proof_size` is therefore admitted by the gateway and forwarded to the mempool as if it were valid.

### Finding Description

`StatelessTransactionValidator::validate()` dispatches invoke-specific sub-validators inside a single `if let` arm:

```rust
if let RpcTransaction::Invoke(invoke_tx) = tx {
    self.validate_client_side_proving_allowed(invoke_tx)?;
    self.validate_proof_facts_and_proof_consistency(invoke_tx)?;
    // validate_proof_size is NOT called here
}
``` [1](#0-0) 

The missing validator is fully implemented and enforces `self.config.max_proof_size`:

```rust
fn validate_proof_size(
    &self,
    tx: &RpcInvokeTransaction,
) -> StatelessTransactionValidatorResult<()> {
    let RpcInvokeTransaction::V3(tx) = tx;
    let proof_size = tx.proof.0.len();
    if proof_size > self.config.max_proof_size {
        return Err(StatelessTransactionValidatorError::ProofTooLarge {
            proof_size,
            max_proof_size: self.config.max_proof_size,
        });
    }
    Ok(())
}
``` [2](#0-1) 

The `StatelessTransactionValidatorConfig` carries `max_proof_size` as a first-class field, confirming the intent to enforce this limit at the gateway boundary. Because the call is absent, the limit is never applied.

The structural analog to the external report is exact:

| External (`vePeg`) | Sequencer (`StatelessTransactionValidator`) |
|---|---|
| `require(_locked.end > block.timestamp)` | `validate_client_side_proving_allowed` + `validate_proof_facts_and_proof_consistency` |
| Missing `\|\| _locked.perpetuallyLocked` | Missing `self.validate_proof_size(invoke_tx)?` |
| Perpetual-lock deposits silently blocked | Oversized-proof invoke transactions silently admitted |

### Impact Explanation

**High. Mempool/gateway/RPC admission accepts invalid transactions before sequencing.**

An attacker submits an `RpcInvokeTransaction::V3` whose `proof` vector contains more elements than `max_proof_size`. The gateway's stateless validator passes it without error. The transaction enters the mempool and is propagated to peers via the P2P layer. Every node that receives it must deserialize and store the oversized proof payload, consuming unbounded memory proportional to the proof length. Because the check is absent at the only admission gate, no downstream component re-applies it before the transaction is sequenced.

### Likelihood Explanation

**Medium.** Constructing an oversized-proof invoke transaction requires no privileged access—any user can submit an `RpcInvokeTransaction::V3` with an arbitrarily long `proof` field through the public gateway endpoint. The only prerequisite is knowledge that `validate_proof_size` is not enforced, which is visible from the public source code.

### Recommendation

Add the missing call inside the invoke-specific validation block:

```diff
 if let RpcTransaction::Invoke(invoke_tx) = tx {
     self.validate_client_side_proving_allowed(invoke_tx)?;
     self.validate_proof_facts_and_proof_consistency(invoke_tx)?;
+    self.validate_proof_size(invoke_tx)?;
 }
``` [1](#0-0) 

### Proof of Concept

1. Obtain the gateway's public `add_transaction` endpoint.
2. Construct a valid `RpcInvokeTransaction::V3` with `proof_facts` and `proof` both non-empty (to pass `validate_proof_facts_and_proof_consistency`) and `allow_client_side_proving = true` (to pass `validate_client_side_proving_allowed`), but set `proof.0.len()` to `max_proof_size + 1`.
3. Submit the transaction. The gateway returns a success response and the transaction enters the mempool.
4. Observe that `StatelessTransactionValidatorError::ProofTooLarge` is never raised, confirming the bound is not enforced. [3](#0-2)

### Citations

**File:** crates/apollo_gateway/src/stateless_transaction_validator.rs (L45-48)
```rust
        if let RpcTransaction::Invoke(invoke_tx) = tx {
            self.validate_client_side_proving_allowed(invoke_tx)?;
            self.validate_proof_facts_and_proof_consistency(invoke_tx)?;
        }
```

**File:** crates/apollo_gateway/src/stateless_transaction_validator.rs (L231-263)
```rust
    fn validate_client_side_proving_allowed(
        &self,
        tx: &RpcInvokeTransaction,
    ) -> StatelessTransactionValidatorResult<()> {
        if self.config.allow_client_side_proving {
            return Ok(());
        }

        // Reject V3 transactions with proofs when client-side proving is disabled.
        let RpcInvokeTransaction::V3(tx) = tx;
        let has_proof_data = !tx.proof_facts.is_empty() || !tx.proof.is_empty();
        if has_proof_data {
            return Err(StatelessTransactionValidatorError::ClientSideProvingNotAllowed);
        }

        Ok(())
    }

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

**File:** crates/apollo_gateway/src/stateless_transaction_validator.rs (L265-278)
```rust
    fn validate_proof_size(
        &self,
        tx: &RpcInvokeTransaction,
    ) -> StatelessTransactionValidatorResult<()> {
        let RpcInvokeTransaction::V3(tx) = tx;
        let proof_size = tx.proof.0.len();
        if proof_size > self.config.max_proof_size {
            return Err(StatelessTransactionValidatorError::ProofTooLarge {
                proof_size,
                max_proof_size: self.config.max_proof_size,
            });
        }
        Ok(())
    }
```
