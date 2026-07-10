### Title
Deprecated `verify_transaction_inclusion` Remains Publicly Callable, Enabling 64-Byte Merkle Proof Forgery — (`contract/src/lib.rs`)

### Summary
The deprecated `verify_transaction_inclusion` function is still a live, unprivileged public entry point on the contract. It omits the coinbase Merkle proof check that was introduced in `verify_transaction_inclusion_v2` specifically to close the 64-byte internal-node forgery path. Any NEAR caller can invoke the v1 path directly, supply an internal Merkle tree node as `tx_id`, and receive a `true` result for a transaction that does not exist — the exact check the contract itself documents as missing.

### Finding Description
`verify_transaction_inclusion` (v1) is marked `#[deprecated]` but carries no access restriction. It is a fully public `#[pause]`-gated view function callable by any NEAR account. [1](#0-0) 

The function's own doc-comment acknowledges the broken invariant:

> This function may return `true` if the provided `tx_id` is a hash of an internal node in the Merkle tree rather than a valid transaction hash. [2](#0-1) 

The fix — requiring a coinbase Merkle proof of equal length and validating it against the block's `merkle_root` before delegating to v1 — exists only in `verify_transaction_inclusion_v2`: [3](#0-2) 

Because v1 is still reachable independently, the coinbase guard is trivially bypassed by calling v1 directly.

The underlying Merkle computation in `merkle-tools` is position-based and has no awareness of whether the leaf it is given is a real transaction hash or an internal node: [4](#0-3) 

The only guard v1 adds beyond the Merkle root comparison is a non-empty proof check: [5](#0-4) 

This does not prevent the forgery; it only prevents a zero-length proof.

### Impact Explanation
A downstream NEAR contract that calls `verify_transaction_inclusion` (v1) to gate a cross-chain action (e.g., releasing bridged assets, confirming a Bitcoin payment) can be deceived. An attacker who knows the Merkle tree of any block already accepted by the light client can compute an internal node hash, choose the correct `tx_index` and `merkle_proof` such that `compute_root_from_merkle_proof(internal_node, index, proof) == block.merkle_root`, and receive `true` for a non-existent transaction. The corrupted proof result is the direct output of a production public API.

**Impact: 3 / 5** — Requires a real accepted block but no privileged role; impact is bounded by what downstream consumers do with the boolean result.

### Likelihood Explanation
The 64-byte internal-node forgery is a well-documented Bitcoin SPV attack (publicly described by BitMEX Research). The contract itself links to the write-up in the v2 doc-comment. The v1 function is still listed in the ABI, so any integrator or attacker inspecting the contract interface will find it. No special capability beyond a NEAR account is required.

**Likelihood: 3 / 5** — Publicly known technique, no privileged access needed, entry point is openly advertised.

### Recommendation
- Remove `verify_transaction_inclusion` (v1) from the public ABI entirely, or gate it with a role that prevents unprivileged calls, so that all callers are forced through the coinbase-validated v2 path.
- If backward compatibility must be preserved, add a `require!(false, "use verify_transaction_inclusion_v2")` panic so the function is unreachable at runtime even if it remains in the ABI.
- Document in the contract's public interface that v1 is unsafe and must not be used by integrators.

### Proof of Concept
1. Identify any block hash `B` present in `mainchain_header_to_height` (public via `get_block_hash_by_height`).
2. Obtain the full transaction list for block `B` from a Bitcoin node (public data).
3. Compute the Merkle tree. Pick any internal node `N` at depth `d` with left-child index `i`. The node `N` is the double-SHA256 of its two children concatenated — exactly 64 bytes, matching the forgery precondition.
4. Construct `merkle_proof` as the sibling path from depth `d` up to the root, with `tx_index` set to `i` at that depth.
5. Call `verify_transaction_inclusion` with `tx_id = N`, `tx_block_blockhash = B`, `tx_index`, `merkle_proof`, `confirmations = 1`.
6. `compute_root_from_merkle_proof(N, i, proof)` returns `block.merkle_root`; the function returns `true` for a transaction `N` that does not exist on-chain. [6](#0-5)

### Citations

**File:** contract/src/lib.rs (L276-280)
```rust
    ///
    /// # Warning
    /// This function may return `true` if the provided `tx_id` is a hash of an internal node in the Merkle tree rather than a valid transaction hash.
    /// We assume that validation of whether the `tx_id` corresponds to a valid transaction hash is performed at a higher level of verification.
    ///
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
