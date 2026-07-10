### Title
Deprecated `verify_transaction_inclusion` Accepts Internal Merkle Tree Nodes as Valid Transaction IDs, Enabling Proof Forgery — (File: `contract/src/lib.rs`)

---

### Summary

The deprecated `verify_transaction_inclusion` function remains publicly callable by any unprivileged NEAR account. It accepts a caller-supplied `tx_id` without validating that it is a leaf node (a real transaction hash) rather than an internal Merkle tree node. An attacker can supply an internal node hash as `tx_id` together with a crafted proof, causing the function to return `true` for a transaction that does not exist in the block. Downstream contracts that consume this result to authorize fund releases are directly exploitable.

---

### Finding Description

`verify_transaction_inclusion` delegates proof checking entirely to `merkle_tools::compute_root_from_merkle_proof`, which is a pure hash-chain computation that treats its first argument (`transaction_hash`) identically regardless of whether it is a leaf or an internal node: [1](#0-0) 

The contract function then compares the computed root against the stored block Merkle root: [2](#0-1) 

Because `compute_root_from_merkle_proof` does not distinguish leaf from internal nodes, an attacker can pass any internal node hash (e.g., the hash of two sibling transactions) as `tx_id`, supply a proof that walks from that internal node up to the root, and the function returns `true`. This is the well-documented 64-byte transaction Merkle proof forgery attack (https://www.bitmex.com/blog/64-Byte-Transactions).

The function's own `# Warning` block acknowledges this: [3](#0-2) 

Despite being marked `#[deprecated]`, the function is still exported as a public NEAR method with no runtime guard: [4](#0-3) 

Rust's `#[deprecated]` attribute emits only a compiler warning; it does not prevent the function from being called at runtime or from being invoked via a NEAR cross-contract call. The `#[pause]` attribute allows a `PauseManager` to disable it, but the function is active by default.

The fixed version, `verify_transaction_inclusion_v2`, mitigates this by first anchoring the Merkle tree structure via a coinbase proof at index 0 before delegating to the v1 logic: [5](#0-4) 

The v1 function remains reachable in parallel, bypassing this mitigation entirely.

---

### Impact Explanation

Any bridge, escrow, or cross-chain application that calls `verify_transaction_inclusion` and releases funds upon a `true` result can be drained. The attacker fabricates a "proof" of a non-existent Bitcoin transaction, the contract confirms it, and the downstream contract releases assets. This is a direct proof-verification forgery with financial impact equivalent to the external report's token drain.

---

### Likelihood Explanation

The attack requires no privileged role, no leaked key, and no social engineering. Any NEAR account can call `verify_transaction_inclusion` directly. The only prerequisite is knowledge of the 64-byte Merkle forgery technique and access to a block already stored in the contract (which is public state). Likelihood is **medium-high** given the function is documented as vulnerable and the attack is well-known.

---

### Recommendation

Remove the public NEAR method export of `verify_transaction_inclusion`, or add a runtime `env::panic_str` guard that unconditionally aborts the call, forcing all callers to migrate to `verify_transaction_inclusion_v2`. A compiler `#[deprecated]` attribute alone is insufficient because it does not prevent on-chain invocation.

---

### Proof of Concept

Given a block already stored in the contract with transactions `[t0, t1, t2, t3]` and Merkle tree:

```
        root
       /    \
      A      B
     / \    / \
    t0  t1 t2  t3
```

1. Compute internal node `A = double_sha256(t0 || t1)`.
2. Call `verify_transaction_inclusion` with:
   - `tx_id = A` (an internal node, not a real transaction)
   - `tx_index = 0`
   - `merkle_proof = [B]`
   - `tx_block_blockhash` = hash of the stored block
   - `confirmations = 1`
3. `compute_root_from_merkle_proof(A, 0, [B])` computes `double_sha256(A || B) = root`.
4. `root == header.block_header.merkle_root` → function returns `true`.
5. No transaction with ID `A` exists in the block; the verification is forged. [6](#0-5)

### Citations

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
