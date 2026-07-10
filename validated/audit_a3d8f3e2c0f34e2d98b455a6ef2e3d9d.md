### Title
Incomplete Merkle Proof Validation in `verify_transaction_inclusion` Permits Internal-Node Substitution Forgery — (File: `contract/src/lib.rs`)

---

### Summary

`verify_transaction_inclusion` validates that a supplied Merkle proof reconstructs the stored Merkle root, but it never validates that the supplied `tx_id` is an actual leaf node. An unprivileged NEAR caller can supply an internal Merkle-tree node hash as `tx_id`, pair it with a shortened proof that correctly walks from that internal node to the root, and receive `true`. Any consumer contract that gates fund release or cross-chain state transitions on this return value is directly exploitable.

---

### Finding Description

The function performs three checks before computing the proof:

1. The target block belongs to the current mainchain.
2. The required confirmation depth is satisfied.
3. The `merkle_proof` vector is non-empty. [1](#0-0) 

It then delegates the entire cryptographic decision to `compute_root_from_merkle_proof`: [2](#0-1) 

`compute_root_from_merkle_proof` is a pure positional hash-chain computation. It accepts any 32-byte value as `transaction_hash` and any `transaction_position`, and it has no concept of tree depth or leaf vs. internal node: [3](#0-2) 

Neither `verify_transaction_inclusion` nor `compute_root_from_merkle_proof` checks that `tx_id` is a leaf. An internal node at depth *d* from the root can be "proved" with a proof of length *d*, and the computed root will equal the stored Merkle root — because the internal node genuinely is part of the tree.

The contract's own documentation acknowledges this gap: [4](#0-3) 

The function is marked deprecated but remains a live, unpermissioned public NEAR method: [5](#0-4) 

The `#[deprecated]` attribute is a Rust compiler hint only; it imposes no runtime restriction. The method is callable by any NEAR account when the contract is not paused.

The analog to the original report is exact: `escapeUnsafeCharacters` escaped backslash and backtick but omitted single-quote, leaving one injection vector open. Here, `verify_transaction_inclusion` validates proof structure and confirmation depth but omits the leaf-node check, leaving one forgery vector open.

---

### Impact Explanation

A consumer contract (bridge, atomic-swap, lending protocol) that calls `verify_transaction_inclusion` and acts on its boolean result can be made to accept a proof for a Bitcoin transaction that does not exist. The attacker does not need to break any hash function; they only need to read the public Merkle tree of any mainchain block and identify an internal node. The forged proof is constructed entirely from public data.

Corrupted invariant: the contract's guarantee that `true` means "the supplied `tx_id` is a confirmed Bitcoin transaction in the canonical chain" is broken. The actual guarantee is only "some 32-byte value at some tree position hashes to the stored Merkle root."

---

### Likelihood Explanation

- The entry path requires no privilege: any NEAR account can call `verify_transaction_inclusion`.
- The required inputs (block hash, internal node hash, sibling hashes) are all publicly readable from the Bitcoin blockchain.
- Any block with more than one transaction contains internal nodes that can be used.
- Consumer contracts that have not yet migrated to `verify_transaction_inclusion_v2` remain exposed.

---

### Recommendation

Remove `verify_transaction_inclusion` from the contract entirely, or gate it behind a role that prevents external callers from using it. The replacement `verify_transaction_inclusion_v2` mitigates the attack by requiring a coinbase proof of equal depth, which forces the proof to span the full tree height and prevents internal-node substitution: [6](#0-5) 

Until removal, add a runtime check that `merkle_proof.len()` is consistent with the claimed `tx_index` (i.e., `tx_index < 2^merkle_proof.len()`), which does not fully close the vulnerability but raises the bar.

---

### Proof of Concept

1. Select any mainchain block `B` with ≥ 4 transactions. Let its Merkle tree have depth `D`.
2. Identify the left child of the root, call it `N` (an internal node at depth 1). Its sibling is the right child `S`.
3. Call `verify_transaction_inclusion` with:
   - `tx_id = N`
   - `tx_block_blockhash = B` (a real mainchain block)
   - `tx_index = 0` (even position → left child)
   - `merkle_proof = [S]` (one sibling, proof of length 1)
   - `confirmations = 1`
4. Inside the contract, `compute_root_from_merkle_proof(N, 0, [S])` computes `double_sha256(N ‖ S)`, which equals the Merkle root stored in `B`.
5. The function returns `true`.
6. A consumer contract that releases funds on a `true` result is drained without any real Bitcoin transaction having occurred. [7](#0-6) [8](#0-7)

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

**File:** merkle-tools/src/lib.rs (L54-60)
```rust
fn compute_hash(first_tx_hash: &H256, second_tx_hash: &H256) -> H256 {
    let mut concat_inputs = Vec::with_capacity(64);
    concat_inputs.extend(first_tx_hash.0);
    concat_inputs.extend(second_tx_hash.0);

    double_sha256(&concat_inputs)
}
```
