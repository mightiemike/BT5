### Title
Deprecated `verify_transaction_inclusion` Remains Publicly Callable and Accepts Forged Merkle Inclusion Proofs - (File: `contract/src/lib.rs`)

### Summary

`verify_transaction_inclusion` (v1) is still a live, unpermissioned NEAR entry point despite being deprecated. It performs no tree-depth validation, so an attacker can supply a `tx_id` equal to the hash of an internal Merkle node together with a shortened proof path, causing the function to return `true` for a Bitcoin transaction that was never included in the block. Any downstream NEAR contract that gates asset release on this return value is directly exploitable.

### Finding Description

`verify_transaction_inclusion` is marked `#[deprecated]` in Rust, which emits a compiler warning but provides **zero runtime restriction**. The function remains `pub` and is reachable by any unprivileged NEAR caller. [1](#0-0) 

The proof verification delegates entirely to `compute_root_from_merkle_proof`: [2](#0-1) 

That function iterates over whatever proof slice is supplied and never validates that the proof length matches the actual depth of the Merkle tree: [3](#0-2) 

Bitcoin's Merkle tree hashes pairs of 32-byte child hashes (64 bytes total) at every internal node using the same `double_sha256` primitive used for transaction IDs. A crafted 64-byte transaction whose serialized form equals the concatenation of two real child hashes will have a `txid` identical to the hash of that internal node. Submitting this `txid` with a proof path that starts one level above the leaves (length = tree_depth − 1) causes `compute_root_from_merkle_proof` to reproduce the correct Merkle root, and the function returns `true`.

The code's own docstring acknowledges this: [4](#0-3) 

The only guard present is a non-empty proof check: [5](#0-4) 

This does not prevent a shortened-but-non-empty proof from verifying an internal node.

`verify_transaction_inclusion_v2` fixes this by requiring a coinbase proof of equal length, anchoring the tree depth: [6](#0-5) 

But v1 is never removed, so the fix is opt-in only.

### Impact Explanation

Any NEAR contract that calls `verify_transaction_inclusion` (v1) to gate a cross-chain action — releasing escrowed tokens, minting wrapped BTC, confirming a deposit — can be made to accept a proof for a Bitcoin transaction that does not exist. The attacker does not need to control any privileged role; they only need to identify a real confirmed block in the light client's main chain and craft the forged proof arguments. The corrupted invariant is: `verify_transaction_inclusion` returns `true` ↔ `tx_id` is a real leaf in the block's Merkle tree. After exploitation, that invariant is broken and the downstream contract's asset-release logic fires on false state.

### Likelihood Explanation

The 64-byte transaction forgery technique is publicly documented (linked in the contract's own deprecation notice). The entry point is open to any NEAR account with no staking or role requirement. The only prerequisite is that a downstream consumer of v1 exists — which is the entire motivation for keeping the function deployed. Likelihood is **high** for any integration that has not migrated to v2.

### Recommendation

1. Remove `verify_transaction_inclusion` (v1) from the contract's public ABI entirely, or gate it with an access-control role so it cannot be called by arbitrary accounts.
2. If removal is not immediately possible, add a `require!(false, "use verify_transaction_inclusion_v2")` body so every call panics at runtime.
3. Audit all known downstream consumers and confirm they call v2 exclusively.
4. Add a regression test that submits an internal-node hash as `tx_id` with a shortened proof and asserts the call is rejected.

### Proof of Concept

```
Given: a real Bitcoin block B tracked by the light client with Merkle tree:

         root
        /    \
      n01    n23
      / \    / \
    tx0 tx1 tx2 tx3

Internal node n01 = double_sha256(tx0 || tx1)

Attacker constructs:
  tx_id             = n01   (hash of internal node, not a real txid)
  tx_block_blockhash = B
  tx_index          = 0     (position of n01 at level 1)
  merkle_proof      = [n23] (one sibling, not two — proof length = 1, not 2)
  confirmations     = 0

compute_root_from_merkle_proof(n01, 0, [n23]):
  step 1: position 0 is even → hash(n01, n23) = root  ✓

verify_transaction_inclusion returns true.

Any contract checking "did tx_id appear in block B" now believes
a transaction that never existed was confirmed.
``` [7](#0-6) [8](#0-7)

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

**File:** contract/src/lib.rs (L315-315)
```rust
        require!(!args.merkle_proof.is_empty(), "Merkle proof is empty");
```

**File:** contract/src/lib.rs (L317-323)
```rust
        // compute merkle tree root and check if it matches block's original merkle tree root
        merkle_tools::compute_root_from_merkle_proof(
            args.tx_id,
            usize::try_from(args.tx_index).unwrap(),
            &args.merkle_proof,
        ) == header.block_header.merkle_root
    }
```

**File:** contract/src/lib.rs (L347-365)
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

**File:** btc-types/src/contract_args.rs (L16-24)
```rust
#[near(serializers = [borsh, json])]
#[derive(Clone, Debug)]
pub struct ProofArgs {
    pub tx_id: H256,
    pub tx_block_blockhash: H256,
    pub tx_index: u64,
    pub merkle_proof: Vec<H256>,
    pub confirmations: u64,
}
```
