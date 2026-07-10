### Title
Deprecated `verify_transaction_inclusion` Remains Publicly Callable, Bypassing the 64-Byte Merkle Proof Forgery Protection — (File: `contract/src/lib.rs`)

### Summary

`verify_transaction_inclusion_v2` was introduced specifically to close the 64-byte transaction Merkle proof forgery vulnerability. However, the original `verify_transaction_inclusion` function is still a live, public, unpermissioned contract method. Any NEAR caller can invoke it directly, completely skipping the coinbase-proof guard that v2 enforces. This is a direct structural analog to the reported pattern: a secure path exists, but the insecure path is never removed and remains reachable.

---

### Finding Description

`verify_transaction_inclusion` is marked `#[deprecated(since = "0.5.0")]` and carries an explicit warning that it may return `true` for an internal Merkle-tree node rather than a real transaction hash. [1](#0-0) 

Despite the deprecation, the function is still decorated with `#[pause]` (not `#[private]`, not removed), making it a fully reachable public method callable by any unprivileged NEAR account. [2](#0-1) 

The v2 replacement adds a mandatory coinbase-proof check before delegating to v1: [3](#0-2) 

The coinbase check forces the caller to prove that a real coinbase transaction sits at index 0 of the same Merkle tree, which defeats the 64-byte forgery because a crafted 64-byte internal node cannot simultaneously satisfy both the coinbase proof and the target-transaction proof.

`verify_transaction_inclusion` (v1) performs no such check: [4](#0-3) 

The only guard is that `merkle_proof` must be non-empty — a trivially satisfied condition.

---

### Impact Explanation

**Impact: High.**

Any downstream NEAR contract or off-chain service that calls `verify_transaction_inclusion` (rather than v2) can be fed a forged proof. An attacker constructs a 64-byte value that is a valid internal Merkle-tree node of a real Bitcoin block, then supplies it as `tx_id` with a valid sibling path. The function computes the Merkle root correctly (because the node genuinely exists in the tree) and returns `true` — asserting that a Bitcoin transaction was included when no such transaction exists. Contracts that gate fund releases, cross-chain bridges, or any state change on this boolean result are directly exploitable.

---

### Likelihood Explanation

**Likelihood: High.**

- No role, stake, or registration is required; any NEAR account can call `verify_transaction_inclusion`.
- The 64-byte forgery technique is publicly documented (BitMEX research, referenced in the contract's own comments at line 267).
- The contract itself acknowledges the risk in the deprecation notice and the `# Warning` block, confirming the attack surface is known and real.
- Downstream integrators may call the deprecated method because it has a simpler interface (no coinbase fields), especially if they integrated before v2 was introduced. [5](#0-4) 

---

### Recommendation

Remove `verify_transaction_inclusion` entirely, or make it revert unconditionally:

```rust
#[deprecated]
pub fn verify_transaction_inclusion(&self, ...) -> bool {
    env::panic_str("verify_transaction_inclusion is removed; use verify_transaction_inclusion_v2");
}
```

This mirrors the recommendation in the external report: override the insecure path to revert so callers are forced onto the safe path.

---

### Proof of Concept

1. Identify any confirmed Bitcoin block `B` stored in the contract's `headers_pool` with at least two transactions. Let its Merkle root be `R` and its transaction list produce internal node `N = SHA256d(tx0 || tx1)` (a 64-byte preimage).

2. Compute a valid Merkle proof `P` that walks from `N` up to `R` (this proof is identical to the proof for `tx0`/`tx1` at the next level up).

3. Call `verify_transaction_inclusion` directly as any unprivileged NEAR account:

```json
{
  "tx_id": "<N — the internal node hash>",
  "tx_block_blockhash": "<hash of block B>",
  "tx_index": "<position of N in the next-level tree>",
  "merkle_proof": ["<siblings from N up to R>"],
  "confirmations": 1
}
```

4. `compute_root_from_merkle_proof(N, position, P)` returns `R`, which equals `header.block_header.merkle_root`. The function returns `true`.

5. No coinbase proof is ever requested. The call succeeds for a transaction that does not exist on the Bitcoin blockchain. [6](#0-5) [7](#0-6)

### Citations

**File:** contract/src/lib.rs (L264-288)
```rust
    ///
    /// # Deprecated
    /// Use [`verify_transaction_inclusion_v2`] instead, which includes coinbase merkle proof validation
    /// to mitigate the 64-byte transaction Merkle proof forgery vulnerability:
    /// https://www.bitmex.com/blog/64-Byte-Transactions
    ///
    /// @param `tx_id` transaction identifier
    /// @param `tx_block_blockhash` block hash at which transacton is supposedly included
    /// @param `tx_index` index of transaction in the block's tx merkle tree
    /// @param `merkle_proof` merkle tree path (concatenated LE sha256 hashes) (does not contain initial `transaction_hash` and `merkle_root`)
    /// @param confirmations how many confirmed blocks we want to have before the transaction is valid
    /// @return True if `tx_id` is at the claimed position in the block at the given blockhash, False otherwise
    ///
    /// # Warning
    /// This function may return `true` if the provided `tx_id` is a hash of an internal node in the Merkle tree rather than a valid transaction hash.
    /// We assume that validation of whether the `tx_id` corresponds to a valid transaction hash is performed at a higher level of verification.
    ///
    /// # Panics
    /// Multiple cases
    #[deprecated(
        since = "0.5.0",
        note = "Use `verify_transaction_inclusion_v2` instead."
    )]
    #[pause]
    pub fn verify_transaction_inclusion(&self, #[serializer(borsh)] args: ProofArgs) -> bool {
```

**File:** contract/src/lib.rs (L315-323)
```rust
        require!(!args.merkle_proof.is_empty(), "Merkle proof is empty");

        // compute merkle tree root and check if it matches block's original merkle tree root
        merkle_tools::compute_root_from_merkle_proof(
            args.tx_id,
            usize::try_from(args.tx_index).unwrap(),
            &args.merkle_proof,
        ) == header.block_header.merkle_root
    }
```

**File:** contract/src/lib.rs (L347-368)
```rust
    pub fn verify_transaction_inclusion_v2(&self, #[serializer(borsh)] args: ProofArgsV2) -> bool {
        require!(
            args.merkle_proof.len() == args.coinbase_merkle_proof.len(),
            "Coinbase merkle proof and transaction merkle proof should have the same length"
        );

        let header = self
            .headers_pool
            .get(&args.tx_block_blockhash)
            .unwrap_or_else(|| env::panic_str("cannot find requested transaction block"));

        require!(
            merkle_tools::compute_root_from_merkle_proof(
                args.coinbase_tx_id.clone(),
                0usize,
                &args.coinbase_merkle_proof,
            ) == header.block_header.merkle_root,
            "Incorrect coinbase merkle proof"
        );

        #[allow(deprecated)]
        self.verify_transaction_inclusion(args.into())
```

**File:** merkle-tools/src/lib.rs (L34-51)
```rust
pub fn compute_root_from_merkle_proof(
    transaction_hash: H256,
    transaction_position: usize,
    merkle_proof: &Vec<H256>,
) -> H256 {
    let mut current_hash = transaction_hash;
    let mut current_position = transaction_position;

    for proof_hash in merkle_proof {
        if current_position % 2 == 0 {
            current_hash = compute_hash(&current_hash, proof_hash);
        } else {
            current_hash = compute_hash(proof_hash, &current_hash);
        }
        current_position /= 2;
    }

    current_hash
```
