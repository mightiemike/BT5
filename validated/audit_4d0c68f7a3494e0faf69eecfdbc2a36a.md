### Title
Deprecated `verify_transaction_inclusion` Accepts Internal Merkle-Tree Node Hashes as Valid Transaction IDs, Enabling Proof Forgery - (File: `contract/src/lib.rs`)

### Summary

`verify_transaction_inclusion` performs no validation that the caller-supplied `tx_id` is a leaf node (real transaction hash) rather than an internal Merkle tree node hash. Any unprivileged NEAR caller can supply a crafted internal-node hash as `tx_id`, pair it with a structurally valid Merkle proof, and receive a `true` return value — falsely asserting that a non-existent transaction was included in a Bitcoin block stored by the contract.

### Finding Description

`verify_transaction_inclusion` is still a live, callable public method despite its `#[deprecated]` annotation. Its only input guards are:

1. `args.confirmations <= self.gc_threshold`
2. The block hash must exist in the mainchain
3. `require!(!args.merkle_proof.is_empty(), "Merkle proof is empty")`

After those checks it computes:

```
compute_root_from_merkle_proof(args.tx_id, tx_index, &args.merkle_proof) == header.block_header.merkle_root
```

There is no check that `args.tx_id` is a leaf-level hash. Because Bitcoin's Merkle tree is built by hashing pairs of 32-byte values, every internal node is itself a 32-byte value indistinguishable in type from a transaction hash. An attacker who knows the Merkle tree of any block already accepted by the contract can pick any internal node `N` at depth `d`, supply `N` as `tx_id`, supply the `d`-element sibling path as `merkle_proof`, and the root comparison will succeed, returning `true`.

The contract's own warning confirms the root cause:

> "This function may return `true` if the provided `tx_id` is a hash of an internal node in the Merkle tree rather than a valid transaction hash. We assume that validation of whether the `tx_id` corresponds to a valid transaction hash is performed at a higher level of verification."

`verify_transaction_inclusion_v2` was introduced to mitigate this via a coinbase-proof anchor, but the original function was never removed and remains callable by any account.

### Impact Explanation

Any bridge contract, payment gateway, or cross-chain application that calls `verify_transaction_inclusion` to gate fund releases or state transitions can be deceived into believing an arbitrary fake Bitcoin transaction was confirmed. The attacker does not need to mine a block or control any privileged role — only knowledge of the Merkle tree of a block already in the contract's `headers_pool` is required. The corrupted proof result (`true`) propagates to every consumer of the API.

**Impact: 3 / 5** — High consequence for downstream consumers (fund theft via bridge), but limited to callers of the deprecated endpoint.

### Likelihood Explanation

The deprecated function is unconditionally reachable by any NEAR account. The 64-byte / internal-node Merkle forgery technique is publicly documented (BitMEX research, CVE-2012-2459 lineage). No privileged access, leaked keys, or social engineering is required. The attacker only needs to observe any block already submitted to the contract.

**Likelihood: 3 / 5** — Straightforward to execute for anyone familiar with Bitcoin Merkle trees; the only friction is that the target block must already be in the contract's pool.

### Recommendation

Remove `verify_transaction_inclusion` entirely from the public API, or add a `#[pause]` guard that is permanently activated for this method. All callers must migrate to `verify_transaction_inclusion_v2`, which anchors the proof to the coinbase transaction and prevents internal-node substitution. If backward compatibility is required, add an explicit check that `merkle_proof.len()` equals `log2(tree_size)` and that `tx_index < 2^merkle_proof.len()`, and document that leaf-vs-internal-node disambiguation remains the caller's responsibility.

### Proof of Concept

1. Observe any block `B` already accepted into `mainchain_header_to_height`. Its `merkle_root` is stored in `headers_pool`.
2. Reconstruct the Merkle tree for `B` (all transaction hashes are public on-chain Bitcoin data).
3. Select any internal node `N` at depth `d` (e.g., the hash of the left subtree root at depth 1).
4. Build the `d`-element sibling path `proof` such that `compute_root_from_merkle_proof(N, index, proof) == merkle_root`.
5. Call:
   ```
   verify_transaction_inclusion({
     tx_id: N,                  // internal node, not a real tx
     tx_block_blockhash: B,
     tx_index: <computed index>,
     merkle_proof: proof,
     confirmations: 1,
   })
   ```
6. The function returns `true`, asserting that the fake "transaction" `N` is confirmed in block `B`. [1](#0-0) [2](#0-1) [3](#0-2) [4](#0-3)

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
