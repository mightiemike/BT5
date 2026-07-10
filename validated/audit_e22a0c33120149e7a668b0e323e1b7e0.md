### Title
Deprecated `verify_transaction_inclusion()` Remains Publicly Callable, Bypassing Coinbase Proof Validation — (File: `contract/src/lib.rs`)

---

### Summary

`verify_transaction_inclusion()` is marked `#[deprecated]` but remains an unrestricted public NEAR contract method. `verify_transaction_inclusion_v2()` was introduced specifically to mitigate the 64-byte transaction Merkle proof forgery vulnerability by requiring a coinbase merkle proof of equal depth. Because the deprecated function is still callable by any unprivileged NEAR account, an attacker can bypass that mitigation entirely and obtain a `true` proof result for a crafted internal Merkle tree node that is not a real transaction.

---

### Finding Description

`verify_transaction_inclusion_v2()` performs coinbase proof validation before delegating to `verify_transaction_inclusion()`: [1](#0-0) 

The coinbase proof check enforces that the proof depth is consistent with the real Merkle tree structure, preventing an attacker from presenting an internal node hash as a leaf (transaction) hash with a shorter proof path.

`verify_transaction_inclusion()` is the function that `_v2` wraps. It is marked `#[deprecated]` but carries no `#[private]` attribute: [2](#0-1) 

In Rust, `#[deprecated]` only emits a compiler warning for callers within the same codebase. It has **no effect** on the deployed NEAR WASM binary: the method remains exported and callable by any external account. The only gate is `#[pause]`, which is a runtime switch controlled by `PauseManager` — not a permanent access restriction.

The Merkle proof computation itself does not validate proof depth against the actual tree height: [3](#0-2) 

The only depth-related check in `verify_transaction_inclusion()` is `require!(!args.merkle_proof.is_empty())` — it does not enforce a minimum or maximum proof length relative to the block's transaction count.

The CLAUDE.md explicitly acknowledges the attack surface but frames it as a caller responsibility: [4](#0-3) 

This framing is insufficient: the contract still exports the vulnerable method as a first-class callable endpoint.

---

### Impact Explanation

The analog to the external report is direct:

| External Report | This Repository |
|---|---|
| `setContracts()` updates state without validation that `Market.setOrderBooks()` etc. enforce | `verify_transaction_inclusion()` verifies proofs without the coinbase depth check that `verify_transaction_inclusion_v2()` enforces |
| Both paths update the same state variables | Both paths produce a `bool` proof result consumed by callers |
| `setContracts()` is still callable, bypassing safety | `verify_transaction_inclusion()` is still callable, bypassing safety |

A recipient contract or bridge application that calls `verify_transaction_inclusion()` (or that an attacker directs to call it) can be made to accept a false transaction inclusion proof. The corrupted value is the **proof result**: `true` is returned for an internal Merkle tree node hash that is not a real transaction. This enables cross-chain fraud in any bridge or payment application that relies on this light client for settlement finality.

---

### Likelihood Explanation

The 64-byte transaction Merkle proof forgery attack is well-documented (referenced in the contract's own docstring and CLAUDE.md). The entry path requires no privileged role: any NEAR account can call `verify_transaction_inclusion()` directly with a crafted `ProofArgs`. The attacker needs only a Bitcoin block with a known Merkle tree structure and the ability to identify an internal node hash to present as a transaction hash with a shortened proof path. This is a realistic, low-barrier attack for anyone familiar with Bitcoin Merkle tree construction.

---

### Recommendation

Remove `verify_transaction_inclusion()` from the public API. Add `#[private]` to prevent external calls, or delete the function entirely since `verify_transaction_inclusion_v2()` already delegates to it internally. The `#[deprecated]` attribute alone provides no on-chain protection.

---

### Proof of Concept

1. Identify a Bitcoin block stored in the contract's `headers_pool` with at least two transactions (so the Merkle tree has at least one internal node).
2. Compute the internal node hash `N` at depth 1 (the parent of the two leaf transaction hashes `T0` and `T1`): `N = SHA256d(T0 || T1)`.
3. Construct `ProofArgs`:
   - `tx_id = N` (the internal node hash, not a real transaction)
   - `tx_block_blockhash` = the block's hash
   - `tx_index = 0`
   - `merkle_proof = []` (empty — `N` is already the Merkle root for a 2-tx block, or use a single-element proof for deeper trees)
4. Call `verify_transaction_inclusion(args)` directly on the NEAR contract.
5. The function returns `true` because `compute_root_from_merkle_proof(N, 0, [])` equals `N`, which equals `header.block_header.merkle_root` for a 2-transaction block — even though `N` is not a real transaction hash.

`verify_transaction_inclusion_v2()` would reject this because the coinbase proof `compute_root_from_merkle_proof(coinbase_tx_id, 0, coinbase_proof)` must also equal the Merkle root with a proof of the **same length**, which forces the attacker to supply a valid coinbase proof of matching depth — defeating the forgery.

### Citations

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

**File:** merkle-tools/src/lib.rs (L33-52)
```rust
#[must_use]
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

**File:** contract/CLAUDE.md (L66-66)
```markdown
**Important**: This function is vulnerable to the standard Bitcoin merkle tree second-preimage attack — it may return `true` for an internal node hash rather than a real transaction hash. Block headers do not contain the transaction count, so proof depth cannot be validated on-chain. Callers MUST validate that the `tx_id` corresponds to a valid transaction (e.g., by verifying raw transaction data) before trusting the inclusion proof.
```
