### Title
Deprecated `verify_transaction_inclusion` Accepts Internal Merkle Node as Valid Transaction Proof, Enabling Proof-Verification Forgery — (File: `contract/src/lib.rs`)

---

### Summary

The deprecated but still-callable `verify_transaction_inclusion` function performs no validation that the supplied `tx_id` is a leaf node (a real transaction hash) rather than an internal Merkle tree node. Any unprivileged NEAR caller can supply an internal node hash as `tx_id` together with a structurally valid sibling proof and receive `true` from the contract, forging a transaction-inclusion proof for a transaction that does not exist.

---

### Finding Description

`verify_transaction_inclusion` is a public, unguarded NEAR contract method. Its only proof check is:

```rust
require!(!args.merkle_proof.is_empty(), "Merkle proof is empty");

merkle_tools::compute_root_from_merkle_proof(
    args.tx_id,
    usize::try_from(args.tx_index).unwrap(),
    &args.merkle_proof,
) == header.block_header.merkle_root
``` [1](#0-0) 

`compute_root_from_merkle_proof` in the Merkle tools library simply hashes the supplied `transaction_hash` upward through the proof path without any check that the starting value is a leaf:

```rust
pub fn compute_root_from_merkle_proof(
    transaction_hash: H256,
    transaction_position: usize,
    merkle_proof: &Vec<H256>,
) -> H256 {
    let mut current_hash = transaction_hash;
    ...
    for proof_hash in merkle_proof { ... }
    current_hash
}
``` [2](#0-1) 

Because the function treats any 32-byte value as a valid starting point, an internal node `H_internal = SHA256d(T_left || T_right)` at tree depth D satisfies the root equation when paired with a proof of length D. The contract's own documentation acknowledges this broken invariant:

> "This function may return `true` if the provided `tx_id` is a hash of an internal node in the Merkle tree rather than a valid transaction hash." [3](#0-2) 

The fixed successor `verify_transaction_inclusion_v2` closes this gap by requiring a coinbase proof of the same length, which pins the tree depth and forces the target to be a leaf. However, the deprecated function remains a live, callable NEAR method — `#[deprecated]` in Rust is a compile-time lint only; it imposes no runtime restriction. [4](#0-3) 

---

### Impact Explanation

Any downstream NEAR contract (bridge, escrow, cross-chain settlement layer) that calls `verify_transaction_inclusion` and releases funds or updates state upon a `true` result can be drained. The attacker does not need to mine a block or control any privileged role; they only need to know the public transaction set of any already-accepted block, which is freely available. The corrupted proof result is the exact invariant broken: the contract asserts a Bitcoin transaction exists when it does not.

---

### Likelihood Explanation

The entry path is fully open: `verify_transaction_inclusion` carries no `#[trusted_relayer]` guard and no role check — only `#[pause]`, which is inactive in normal operation. [5](#0-4) 

The computation required from the attacker is trivial: read the public transaction list of any mainchain block, compute one level of SHA256d to obtain an internal node, and submit it. Any existing consuming contract that has not yet migrated to `verify_transaction_inclusion_v2` is immediately exploitable.

---

### Recommendation

Remove `verify_transaction_inclusion` from the deployed contract entirely, or gate it with an explicit panic so it is unreachable at runtime. Consuming contracts must be migrated to `verify_transaction_inclusion_v2`, which enforces equal-length coinbase and transaction proofs, pinning the tree depth and preventing internal-node substitution. [6](#0-5) 

---

### Proof of Concept

Consider a mainchain block whose Merkle tree contains four transactions `[T0, T1, T2, T3]`:

```
Root  = SHA256d(H01 || H23)
H01   = SHA256d(T0 || T1)      ← internal node at depth 1
H23   = SHA256d(T2 || T3)
```

**Attacker call** (no special role required):

```
verify_transaction_inclusion({
    tx_id:             H01,          // internal node, not a real txid
    tx_block_blockhash: <any mainchain block hash>,
    tx_index:          0,
    merkle_proof:      [H23],        // single sibling — proof length 1
    confirmations:     1,
})
```

**Execution inside `compute_root_from_merkle_proof`**:

```
current_hash     = H01
current_position = 0   (even → left child)
iteration 1: current_hash = SHA256d(H01 || H23) = Root
return Root
```

`Root == header.block_header.merkle_root` → function returns **`true`**.

The contract has confirmed the inclusion of a transaction that does not exist. A bridge contract acting on this result would release funds to the attacker. [7](#0-6) [8](#0-7)

### Citations

**File:** contract/src/lib.rs (L278-279)
```rust
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

**File:** contract/src/lib.rs (L315-322)
```rust
        require!(!args.merkle_proof.is_empty(), "Merkle proof is empty");

        // compute merkle tree root and check if it matches block's original merkle tree root
        merkle_tools::compute_root_from_merkle_proof(
            args.tx_id,
            usize::try_from(args.tx_index).unwrap(),
            &args.merkle_proof,
        ) == header.block_header.merkle_root
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

**File:** btc-types/src/contract_args.rs (L18-24)
```rust
pub struct ProofArgs {
    pub tx_id: H256,
    pub tx_block_blockhash: H256,
    pub tx_index: u64,
    pub merkle_proof: Vec<H256>,
    pub confirmations: u64,
}
```
