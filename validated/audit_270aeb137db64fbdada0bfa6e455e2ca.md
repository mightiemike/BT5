### Title
`verify_transaction_inclusion` Accepts Unvalidated User-Supplied `tx_id`, Enabling Merkle Proof Forgery — (File: `contract/src/lib.rs`)

---

### Summary

The deprecated `verify_transaction_inclusion` function remains publicly callable by any unprivileged NEAR account. It accepts a user-supplied `tx_id` without validating that it corresponds to a real transaction leaf node. An attacker can supply an internal Merkle tree node hash as `tx_id` together with a valid sibling path, causing the function to return `true` for a transaction that does not exist. Downstream contracts that gate fund releases on this result are directly exploitable.

---

### Finding Description

`verify_transaction_inclusion` in `contract/src/lib.rs` accepts `ProofArgs` — including `tx_id`, `tx_index`, and `merkle_proof` — from any caller and computes:

```
compute_root_from_merkle_proof(tx_id, tx_index, merkle_proof) == header.merkle_root
``` [1](#0-0) 

The function itself documents the broken invariant:

> "This function may return `true` if the provided `tx_id` is a hash of an internal node in the Merkle tree rather than a valid transaction hash." [2](#0-1) 

`compute_root_from_merkle_proof` in `merkle-tools/src/lib.rs` is a pure path-traversal function. It takes `transaction_hash` and walks up the tree using the supplied siblings. It has no mechanism to distinguish a leaf from an internal node. [3](#0-2) 

Because any internal node at depth `D-1` is a valid starting point for a `D-1`-step path to the root, an attacker can supply that internal node hash as `tx_id` with a proof of `D-1` siblings and the function will compute the correct Merkle root and return `true`.

The function carries `#[deprecated]` but **no on-chain access restriction**. The only gate is `#[pause]`, which is inactive in normal operation. `submit_blocks` is protected by `#[trusted_relayer]`, but `verify_transaction_inclusion` is not. [4](#0-3) 

The replacement function `verify_transaction_inclusion_v2` closes this gap by requiring a coinbase proof of the same depth as the target proof, which forces the target proof depth to equal the actual leaf depth. However, v1 remains live and callable. [5](#0-4) 

---

### Impact Explanation

Any NEAR contract or account that calls `verify_transaction_inclusion` to gate a privileged action (e.g., releasing bridged BTC, minting wrapped tokens, authorizing a withdrawal) can be deceived into authorising that action for a Bitcoin transaction that never existed. The attacker supplies a hash of a real internal Merkle node from any confirmed block; the contract returns `true`; the downstream contract releases funds. The corrupted proof result is the exact value that authorization logic depends on.

---

### Likelihood Explanation

Every Bitcoin block with more than one transaction contains internal Merkle nodes. Their hashes are public. No cryptographic work is required: the attacker reads the Merkle tree of any confirmed block, picks any internal node `H_internal` at depth `D-1`, computes the `D-1` sibling hashes that form the path to the root, and submits them. The attack is fully deterministic and requires no special privileges, no staking, and no coordination. The only prerequisite is that the target block is already stored in the contract's `headers_pool`.

---

### Recommendation

1. **Remove or hard-disable `verify_transaction_inclusion` (v1).** A `#[deprecated]` attribute is a compile-time hint only; it does not prevent on-chain calls. The function should either be deleted or replaced with a body that unconditionally panics.
2. **Direct all callers to `verify_transaction_inclusion_v2`**, which enforces equal proof depths via the coinbase proof length check, preventing the internal-node substitution attack.
3. If v1 must be retained for backward compatibility, add an explicit check that `merkle_proof.len()` equals the expected tree depth derived from the block's transaction count, or require a coinbase proof as v2 does.

---

### Proof of Concept

Consider a confirmed Bitcoin block stored in the contract with four transactions `T0, T1, T2, T3` and Merkle tree:

```
Root = H(H01, H23)
H01  = H(T0, T1)      ← internal node at depth 1
H23  = H(T2, T3)
```

**Attacker steps:**

1. Read `H01` and `H23` from the public Bitcoin block data.
2. Call `verify_transaction_inclusion` with:
   - `tx_id        = H01`   (internal node, not a real transaction)
   - `tx_index     = 0`
   - `merkle_proof = [H23]` (one sibling, the path from depth-1 to root)
   - `tx_block_blockhash` = hash of the target block (already in `headers_pool`)
   - `confirmations` = any valid value

3. The contract executes:
   ```
   compute_root_from_merkle_proof(H01, 0, [H23])
     → H(H01, H23)   // position 0 is even, so H01 goes left
     → Root           // matches header.merkle_root ✓
   ```
4. `verify_transaction_inclusion` returns **`true`**.

No real transaction with hash `H01` exists. Any downstream contract that releases funds upon receiving `true` from this call is exploited. [6](#0-5) [7](#0-6)

### Citations

**File:** contract/src/lib.rs (L276-280)
```rust
    ///
    /// # Warning
    /// This function may return `true` if the provided `tx_id` is a hash of an internal node in the Merkle tree rather than a valid transaction hash.
    /// We assume that validation of whether the `tx_id` corresponds to a valid transaction hash is performed at a higher level of verification.
    ///
```

**File:** contract/src/lib.rs (L283-288)
```rust
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
