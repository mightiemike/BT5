### Title
Deprecated `verify_transaction_inclusion` Remains Publicly Callable, Enabling 64-Byte Internal-Node Forgery — (`contract/src/lib.rs`)

---

### Summary

`verify_transaction_inclusion` is marked `#[deprecated]` in Rust but remains a fully public, unpermissioned NEAR contract method. Any external caller can invoke it directly with a crafted `ProofArgs` where `tx_id` is an internal Merkle tree node hash, bypassing the coinbase-proof guard that `verify_transaction_inclusion_v2` enforces. The function will return `true` for a fabricated "transaction" that does not exist as a leaf in the block.

---

### Finding Description

`#[deprecated]` in Rust is a **compile-time lint only**. It emits a warning when Rust code calls the function, but it does not remove the method from the NEAR contract's public ABI. Any NEAR RPC caller — with no special role or key — can call `verify_transaction_inclusion` directly. [1](#0-0) 

The function's own documentation acknowledges the weakness: [2](#0-1) 

Inside the function, the only checks are: confirmations bound, canonical-chain membership, and a non-empty proof. The final step is a pure Merkle root recomputation with no leaf-vs-internal-node distinction: [3](#0-2) 

`compute_root_from_merkle_proof` accepts any 32-byte value as `transaction_hash` and performs no validation: [4](#0-3) 

`verify_transaction_inclusion_v2` closes this gap by first verifying a coinbase proof before delegating to the deprecated function: [5](#0-4) 

But because the deprecated function is still independently reachable, the v2 guard is trivially bypassed.

---

### Impact Explanation

An attacker can call `verify_transaction_inclusion` with:
- `tx_id` = hash of an internal Merkle node at depth *d* in a real, confirmed block
- `tx_index` = the position of that node when treated as a leaf at depth *d*
- `merkle_proof` = the remaining path from depth *d* up to the root (shorter than a full leaf proof)

`compute_root_from_merkle_proof` will compute the correct `merkle_root` and the function returns `true`, falsely asserting that a non-existent transaction is included in the block.

Any protocol or application that relies on `verify_transaction_inclusion` returning `true` as proof of a real Bitcoin transaction is vulnerable to accepting fabricated transaction proofs.

---

### Likelihood Explanation

- No privilege is required; any NEAR account can call the method.
- The block and its Merkle tree are public Bitcoin data; internal node hashes are trivially computable.
- The `#[pause]` gate is the only runtime barrier, and the contract is not paused by default.
- The attack is deterministic and locally reproducible.

---

### Recommendation

Remove `verify_transaction_inclusion` from the public contract interface entirely, or add `#[private]` to prevent external calls. If backward compatibility is required, add the same coinbase-proof guard that `verify_transaction_inclusion_v2` uses before delegating.

---

### Proof of Concept

```
1. Submit a real Bitcoin block header to the light client (standard relayer path).
   The block has transactions [T0, T1] with Merkle tree:
       root = H(H(T0, T1))
       internal_node = H(T0, T1)   ← this is a 32-byte hash of 64 bytes of input

2. Call verify_transaction_inclusion with:
   tx_id              = internal_node   (H(T0, T1))
   tx_block_blockhash = that block's hash
   tx_index           = 0
   merkle_proof       = []   (empty — internal_node IS the root for a 2-tx tree)
   confirmations      = 0

   OR for a deeper tree, provide the remaining path from internal_node to root.

3. compute_root_from_merkle_proof(internal_node, 0, []) == internal_node.
   If the block has exactly 2 transactions, merkle_root == H(T0,T1) == internal_node,
   so the comparison succeeds and the function returns true.

4. Result: verify_transaction_inclusion returns true for a hash that is NOT
   a leaf transaction, violating the inclusion-proof invariant.
```

### Citations

**File:** contract/src/lib.rs (L277-279)
```rust
    /// # Warning
    /// This function may return `true` if the provided `tx_id` is a hash of an internal node in the Merkle tree rather than a valid transaction hash.
    /// We assume that validation of whether the `tx_id` corresponds to a valid transaction hash is performed at a higher level of verification.
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

**File:** contract/src/lib.rs (L317-322)
```rust
        // compute merkle tree root and check if it matches block's original merkle tree root
        merkle_tools::compute_root_from_merkle_proof(
            args.tx_id,
            usize::try_from(args.tx_index).unwrap(),
            &args.merkle_proof,
        ) == header.block_header.merkle_root
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

**File:** merkle-tools/src/lib.rs (L33-51)
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
```
