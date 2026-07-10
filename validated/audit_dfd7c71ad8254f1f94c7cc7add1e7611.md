### Title
Deprecated `verify_transaction_inclusion` Accepts Internal Merkle Node Hashes as Valid Transaction IDs - (File: `contract/src/lib.rs`)

---

### Summary

The deprecated `verify_transaction_inclusion` function remains publicly callable on-chain and enforces no policy distinguishing real leaf-level transaction hashes from internal Merkle tree node hashes. Any unprivileged NEAR caller can supply an internal node hash as `tx_id` with a shortened proof path and receive a `true` verification result for a transaction that does not exist.

---

### Finding Description

`verify_transaction_inclusion` computes a Merkle root from the caller-supplied `tx_id` and `merkle_proof` and compares it to the stored block header's `merkle_root`: [1](#0-0) 

The only guard on the proof is: [2](#0-1) 

There is no check that `tx_id` is a leaf-level hash (a real transaction) rather than an internal node of the Merkle tree. The `compute_root_from_merkle_proof` function in `merkle-tools` is a pure positional hash-chain computation: [3](#0-2) 

It accepts any starting hash and any proof length. If an attacker supplies an internal node hash at depth D from the root, a proof of length D (instead of the full leaf-depth proof) will correctly reconstruct the root, and the function returns `true`.

The function is still a public NEAR method — `#[deprecated]` in Rust is a compiler lint only; it does not restrict on-chain callability. It carries no `#[private]` or `#[trusted_relayer]` guard: [4](#0-3) 

The v2 replacement fixes this by requiring a coinbase proof of the same length as the transaction proof, which forces the proof to be leaf-depth and prevents shortened internal-node proofs: [5](#0-4) 

But v1 remains callable and unprotected.

---

### Impact Explanation

Any recipient NEAR contract that calls `verify_transaction_inclusion` to authorize an action (e.g., releasing bridged funds, minting wrapped tokens, confirming a cross-chain payment) will accept a fabricated proof. The attacker does not need to forge a Bitcoin transaction or break any cryptographic primitive — they only need to read the public Merkle tree of any block already stored in the contract's mainchain. The core SPV invariant — that a `true` result certifies a real transaction was mined — is broken for all callers of the v1 API.

**Impact: 3 / 5**

---

### Likelihood Explanation

The attack requires no privileges, no mining power, and no cryptographic computation. All internal node hashes are publicly derivable from the Bitcoin blockchain. The proof construction is a single call to `compute_root_from_merkle_proof` with public data. Any NEAR account can execute this at any time the contract is not paused.

**Likelihood: 4 / 5**

---

### Recommendation

Remove `verify_transaction_inclusion` from the public ABI or gate it with `#[private]` / a role check so it is no longer callable by unprivileged accounts. All integrators must migrate to `verify_transaction_inclusion_v2`. Alternatively, add an explicit check inside v1 that the proof length equals `ceil(log2(block_tx_count))`, or validate the coinbase proof inline before delegating.

---

### Proof of Concept

Consider a block stored in the contract's mainchain with exactly 4 transactions `[tx0, tx1, tx2, tx3]` and Merkle tree:

```
         Root
        /    \
       L      R
      / \    / \
    tx0 tx1 tx2 tx3
```

where `L = hash(tx0, tx1)` and `R = hash(tx2, tx3)` and `Root = hash(L, R)`.

**Attack call:**

```
verify_transaction_inclusion({
    tx_id:              L,          // internal node hash, not a real transaction
    tx_block_blockhash: <block>,
    tx_index:           0,          // even → hash(current, proof[0])
    merkle_proof:       [R],        // one-element proof, not leaf-depth
    confirmations:      1,
})
```

**Execution trace inside `compute_root_from_merkle_proof`:**

- `current_hash = L`, `current_position = 0`
- Iteration 1: position is even → `current_hash = hash(L, R) = Root`
- Loop ends; returns `Root`

`Root == header.block_header.merkle_root` → function returns **`true`**. [6](#0-5) 

The fabricated `tx_id = L` is accepted as a confirmed transaction, despite `L` being an internal Merkle node and no such transaction existing on Bitcoin.

### Citations

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
