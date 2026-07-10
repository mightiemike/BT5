### Title
Deprecated `verify_transaction_inclusion` Remains Publicly Callable, Enabling 64-Byte Merkle Proof Forgery Bypass — (File: `contract/src/lib.rs`)

---

### Summary

`verify_transaction_inclusion` (v1) is still a live, public NEAR entry point. Any unprivileged caller can invoke it directly, bypassing the coinbase-proof anchor that `verify_transaction_inclusion_v2` requires to prevent the 64-byte transaction Merkle proof forgery attack. This is a direct structural analog to the Juiced Audit 04 finding: a function that should only be called with one "type" of proof (v2-validated, coinbase-anchored) silently accepts the wrong type (raw, unanchored), producing an incorrect verification result.

---

### Finding Description

`verify_transaction_inclusion_v2` was introduced to mitigate the 64-byte transaction Merkle proof forgery vulnerability. It does so by first validating a coinbase proof at index 0, which anchors the Merkle tree structure and prevents an attacker from substituting an internal Merkle tree node for a leaf-level transaction hash. [1](#0-0) 

However, `verify_transaction_inclusion` (v1) is still declared `pub` with only a `#[pause]` gate — it is **not** `#[private]`. Any unprivileged NEAR account can call it directly, supplying an internal Merkle tree node as `tx_id` without providing any coinbase proof. [2](#0-1) 

The v1 function only checks that the merkle proof is non-empty and that the computed root matches the block's merkle root: [3](#0-2) 

It does **not** validate that `tx_id` is a leaf-level transaction hash. The code itself documents this broken invariant:

> "This function may return `true` if the provided `tx_id` is a hash of an internal node in the Merkle tree rather than a valid transaction hash." [4](#0-3) 

The `compute_root_from_merkle_proof` function in `merkle-tools` is purely positional — it has no mechanism to distinguish a leaf hash from an internal node hash: [5](#0-4) 

The analog to the external report is exact: just as `withdraw_mercurial` accepted a pool initialized with the wrong strategy type (no explicit strategy-type guard), `verify_transaction_inclusion` accepts the wrong proof type (no explicit leaf-vs-internal-node guard). In both cases the function proceeds with an incompatible input and produces an incorrect result.

---

### Impact Explanation

Any downstream NEAR contract that gates fund releases, bridge withdrawals, or state transitions on the return value of `verify_transaction_inclusion` (v1) can be deceived into accepting a transaction that was never included in the block. The broken invariant is concrete and scoped: `verify_transaction_inclusion` returns `true` for an internal Merkle tree node passed as `tx_id`, not a valid transaction hash. Consumer contracts have no way to distinguish this false positive from a legitimate verification.

---

### Likelihood Explanation

The v1 function is publicly callable by any NEAR account without any role or stake requirement. The 64-byte transaction forgery attack is a well-documented, practically demonstrated technique (cited in the contract's own deprecation notice). An attacker needs only to identify a target block, construct a 64-byte blob whose double-SHA256 hash equals an internal Merkle node in that block, and supply a matching sibling proof path. No privileged access, key material, or social engineering is required.

---

### Recommendation

Remove `verify_transaction_inclusion` (v1) from the public ABI entirely, or gate it with `#[private]` so it is only callable by the contract itself (as an internal helper for `verify_transaction_inclusion_v2`). If backward compatibility with existing callers is required, add an explicit guard that rejects any `tx_index == 0` call that does not also supply a matching coinbase proof, mirroring the v2 logic.

---

### Proof of Concept

1. Attacker identifies a block `B` stored in the light client's mainchain with merkle root `R`.
2. Attacker finds (or constructs) a 64-byte blob `F` such that `double_sha256(F)` equals an internal Merkle node `N` in block `B`'s transaction tree.
3. Attacker computes the sibling proof path from `N` up to `R` — this is a valid Merkle proof for `N` at its position.
4. Attacker calls `verify_transaction_inclusion` with `tx_id = double_sha256(F)`, `tx_block_blockhash = B`, `tx_index = <position of N>`, and the sibling proof path.
5. `compute_root_from_merkle_proof` reconstructs `R` from the internal node hash and the proof path; the comparison `== header.block_header.merkle_root` passes.
6. The function returns `true` for a transaction that does not exist.
7. Any consumer contract that calls `verify_transaction_inclusion` and acts on the `true` result (e.g., releasing bridged funds) is exploited. [6](#0-5) [5](#0-4)

### Citations

**File:** contract/src/lib.rs (L276-281)
```rust
    ///
    /// # Warning
    /// This function may return `true` if the provided `tx_id` is a hash of an internal node in the Merkle tree rather than a valid transaction hash.
    /// We assume that validation of whether the `tx_id` corresponds to a valid transaction hash is performed at a higher level of verification.
    ///
    /// # Panics
```

**File:** contract/src/lib.rs (L283-323)
```rust
    #[deprecated(
        since = "0.5.0",
        note = "Use `verify_transaction_inclusion_v2` instead."
    )]
    #[pause]
    pub fn verify_transaction_inclusion(&self, #[serializer(borsh)] args: ProofArgs) -> bool {
        require!(
            args.confirmations <= self.gc_threshold,
            "The required number of confirmations exceeds the number of blocks stored in memory"
        );

        let heaviest_block_header = self
            .headers_pool
            .get(&self.mainchain_tip_blockhash)
            .unwrap_or_else(|| env::panic_str(ERR_KEY_NOT_EXIST));
        let target_block_height = self
            .mainchain_header_to_height
            .get(&args.tx_block_blockhash)
            .unwrap_or_else(|| env::panic_str("block does not belong to the current main chain"));

        // Check requested confirmations. No need to compute proof if insufficient confirmations.
        require!(
            (heaviest_block_header.block_height).saturating_sub(target_block_height) + 1
                >= args.confirmations,
            "Not enough blocks confirmed"
        );

        let header = self
            .headers_pool
            .get(&args.tx_block_blockhash)
            .unwrap_or_else(|| env::panic_str("cannot find requested transaction block"));

        require!(!args.merkle_proof.is_empty(), "Merkle proof is empty");

        // compute merkle tree root and check if it matches block's original merkle tree root
        merkle_tools::compute_root_from_merkle_proof(
            args.tx_id,
            usize::try_from(args.tx_index).unwrap(),
            &args.merkle_proof,
        ) == header.block_header.merkle_root
    }
```

**File:** contract/src/lib.rs (L347-369)
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
    }
```

**File:** merkle-tools/src/lib.rs (L34-52)
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
}
```
