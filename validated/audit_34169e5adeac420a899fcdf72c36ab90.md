### Title
Unprivileged Caller Can Forge Transaction Inclusion via Internal Merkle Node in `verify_transaction_inclusion` — (`contract/src/lib.rs`)

---

### Summary

`verify_transaction_inclusion` is a publicly callable NEAR contract method that performs no check distinguishing a leaf-level transaction hash from an internal Merkle tree node. Any unprivileged caller can submit an internal node hash as `tx_id` with a crafted shorter proof and receive `true`, falsely proving inclusion of a non-existent transaction in a canonical block.

---

### Finding Description

The function `verify_transaction_inclusion` accepts `ProofArgs` containing `tx_id`, `tx_block_blockhash`, `tx_index`, `merkle_proof`, and `confirmations`. Its only guards are:

1. `confirmations <= gc_threshold`
2. `tx_block_blockhash` must be in the canonical mainchain
3. Enough confirmations exist
4. `merkle_proof` is non-empty [1](#0-0) 

After these checks, it delegates directly to `compute_root_from_merkle_proof`: [2](#0-1) 

`compute_root_from_merkle_proof` treats whatever is passed as `transaction_hash` as the starting hash and walks up the tree — it performs no validation that the input is a leaf: [3](#0-2) 

The `#[deprecated]` attribute is a **compile-time Rust warning only** — it does not prevent on-chain invocation. The function remains fully callable by any NEAR account.

The code itself explicitly documents the flaw:

> "This function may return `true` if the provided `tx_id` is a hash of an internal node in the Merkle tree rather than a valid transaction hash." [4](#0-3) 

---

### Impact Explanation

For a 4-transaction block with leaf hashes `tx0, tx1, tx2, tx3`:

- Level-1 internal node: `node01 = double_sha256(tx0 || tx1)`
- Level-1 internal node: `node23 = double_sha256(tx2 || tx3)`
- Merkle root: `root = double_sha256(node01 || node23)`

An attacker submits:
- `tx_id = node01`
- `tx_index = 0`
- `merkle_proof = [node23]`

`compute_root_from_merkle_proof(node01, 0, [node23])` computes `double_sha256(node01 || node23) = root`, which equals `header.block_header.merkle_root` → returns `true`.

The attacker has proven inclusion of a transaction that does not exist. Any bridge, cross-chain protocol, or downstream consumer relying on this function to authorize asset releases or state transitions is deceived.

---

### Likelihood Explanation

- No access control beyond `#[pause]` — any NEAR account can call this
- The math is trivial: the attacker only needs to know the real Merkle tree structure of any canonical block (publicly available from Bitcoin RPC)
- The `verify_transaction_inclusion_v2` fix exists but does not remove or gate the vulnerable v1 path [5](#0-4) 

---

### Recommendation

Remove or hard-disable `verify_transaction_inclusion` at the contract level (e.g., always panic), rather than relying on a Rust `#[deprecated]` attribute that has no runtime effect. All callers must be migrated to `verify_transaction_inclusion_v2`, which validates the coinbase proof to bound the tree depth and prevent internal-node substitution. [6](#0-5) 

---

### Proof of Concept

```rust
// 4-tx Merkle tree
let tx0 = H256([0x01; 32]);
let tx1 = H256([0x02; 32]);
let tx2 = H256([0x03; 32]);
let tx3 = H256([0x04; 32]);

// Internal node at level 1
let node01 = compute_hash(&tx0, &tx1); // double_sha256(tx0 || tx1)
let node23 = compute_hash(&tx2, &tx3); // double_sha256(tx2 || tx3)
let root   = compute_hash(&node01, &node23);

// Attacker submits node01 as tx_id with 1-element proof [node23]
let result = compute_root_from_merkle_proof(node01.clone(), 0, &vec![node23]);
assert_eq!(result, root); // true — forgery succeeds
```

The block's `merkle_root` field equals `root`, so `verify_transaction_inclusion` returns `true` for the forged internal node `node01` presented as a transaction. [7](#0-6)

### Citations

**File:** contract/src/lib.rs (L278-279)
```rust
    /// This function may return `true` if the provided `tx_id` is a hash of an internal node in the Merkle tree rather than a valid transaction hash.
    /// We assume that validation of whether the `tx_id` corresponds to a valid transaction hash is performed at a higher level of verification.
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
