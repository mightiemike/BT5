### Title
Deprecated `verify_transaction_inclusion` Remains Publicly Callable, Bypassing the 64-Byte Forgery Protection Added in v2 — (`contract/src/lib.rs`)

---

### Summary

`verify_transaction_inclusion` (v1) is still a live, unrestricted public entry point on the contract. The v2 function (`verify_transaction_inclusion_v2`) was introduced specifically to close the 64-byte transaction Merkle proof forgery vulnerability, but any unprivileged NEAR caller can bypass that protection entirely by invoking v1 directly. The `#[deprecated]` attribute is a Rust compiler hint only — it imposes no runtime restriction.

---

### Finding Description

`verify_transaction_inclusion_v2` adds a mandatory coinbase Merkle proof check before delegating to v1:

```rust
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
``` [1](#0-0) 

This coinbase check is the sole mitigation for the 64-byte forgery attack. However, v1 is still decorated only with `#[deprecated]` and `#[pause]` — neither of which restricts who can call it:

```rust
#[deprecated(since = "0.5.0", note = "Use `verify_transaction_inclusion_v2` instead.")]
#[pause]
pub fn verify_transaction_inclusion(&self, #[serializer(borsh)] args: ProofArgs) -> bool {
``` [2](#0-1) 

The only guard inside v1 is a non-empty proof check:

```rust
require!(!args.merkle_proof.is_empty(), "Merkle proof is empty");
``` [3](#0-2) 

This does not prevent the 64-byte forgery. The attack works by supplying an internal Merkle tree node as `tx_id`. Because `compute_root_from_merkle_proof` simply walks the proof path without any depth or leaf-position validation, an internal node at any level can be presented as a "transaction hash" and will produce a valid root:

```rust
pub fn compute_root_from_merkle_proof(
    transaction_hash: H256,
    transaction_position: usize,
    merkle_proof: &Vec<H256>,
) -> H256 {
    let mut current_hash = transaction_hash;
    let mut current_position = transaction_position;
    for proof_hash in merkle_proof {
        ...
    }
    current_hash
}
``` [4](#0-3) 

`ProofArgs` (the v1 argument type) has no `coinbase_tx_id` or `coinbase_merkle_proof` fields, so the coinbase check is structurally impossible to perform through v1:

```rust
pub struct ProofArgs {
    pub tx_id: H256,
    pub tx_block_blockhash: H256,
    pub tx_index: u64,
    pub merkle_proof: Vec<H256>,
    pub confirmations: u64,
}
``` [5](#0-4) 

---

### Impact Explanation

Any downstream NEAR contract or off-chain system that calls `verify_transaction_inclusion` (v1) and receives `true` can be deceived into believing a Bitcoin transaction was included in a block when it was not. Concretely:

- A cross-chain bridge or token-unlock contract that gates fund release on a `true` result from this function can be drained by an attacker who forges a 64-byte internal-node proof.
- The invariant broken is: *"a `true` result from the contract's transaction-inclusion API means the supplied `tx_id` is a real leaf-level transaction committed to the block's Merkle root."* v1 does not enforce this invariant.

---

### Likelihood Explanation

High. v1 is a `pub` function with no access-control role, no privileged caller requirement, and no runtime guard that distinguishes it from v2. Any NEAR account can call it. The 64-byte forgery technique is publicly documented (https://www.bitmex.com/blog/64-Byte-Transactions) and the construction of a valid forged proof requires only knowledge of the target block's Merkle tree structure, which is public on-chain Bitcoin data.

---

### Recommendation

Remove `verify_transaction_inclusion` (v1) from the public ABI entirely, or gate it with an access-control role that prevents unprivileged callers from using it. The safest fix is to make the function `pub(crate)` or remove it, forcing all callers to use `verify_transaction_inclusion_v2`.

---

### Proof of Concept

1. A block `B` is submitted to the contract and accepted into the mainchain. Its Merkle root commits to transactions `[T0, T1]`. The internal node `N = SHA256d(T0 || T1)` equals the Merkle root.
2. An attacker picks any two real hashes `A`, `B` such that `SHA256d(A || B) == N` (or simply uses `T0` and `T1` as the proof siblings for a subtree node one level up in a larger tree).
3. The attacker calls `verify_transaction_inclusion` (v1) with:
   - `tx_id` = the internal node hash (64-byte forgery)
   - `tx_block_blockhash` = block `B`'s hash
   - `tx_index` = a position consistent with the forged proof path
   - `merkle_proof` = a non-empty path that reconstructs the Merkle root from the internal node
4. `compute_root_from_merkle_proof` walks the path and returns the correct Merkle root.
5. The function returns `true` for a transaction that does not exist.
6. Any downstream contract gating on this result releases funds or takes irreversible action based on a forged proof.

The coinbase check in v2 — which would have caught this by requiring a valid depth-0 coinbase proof of the same length — is never executed because the attacker called v1 directly, exactly as the M-08 analog bypassed the `_otherTokenAddress != tokenAddress` check by using the token's second address.

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

**File:** contract/src/lib.rs (L315-315)
```rust
        require!(!args.merkle_proof.is_empty(), "Merkle proof is empty");
```

**File:** contract/src/lib.rs (L358-368)
```rust
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

**File:** btc-types/src/contract_args.rs (L17-24)
```rust
#[derive(Clone, Debug)]
pub struct ProofArgs {
    pub tx_id: H256,
    pub tx_block_blockhash: H256,
    pub tx_index: u64,
    pub merkle_proof: Vec<H256>,
    pub confirmations: u64,
}
```
