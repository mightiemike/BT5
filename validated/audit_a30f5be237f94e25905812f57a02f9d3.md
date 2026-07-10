### Title
Incomplete Coinbase Proof Validation in `verify_transaction_inclusion_v2` Allows Merkle Proof Forgery — (File: `contract/src/lib.rs`)

---

### Summary

`verify_transaction_inclusion_v2` adds a coinbase proof requirement to mitigate the 64-byte Merkle proof forgery attack, but the caller-supplied `coinbase_tx_id` is never verified to be the actual coinbase transaction of the block. An unprivileged NEAR caller can supply an internal Merkle tree node as `coinbase_tx_id` with a shortened proof, satisfy the same-length requirement, and simultaneously use another internal node as the claimed `tx_id`. All three checks pass, yet the "proven" transaction does not exist in the block.

---

### Finding Description

`verify_transaction_inclusion_v2` enforces three conditions before delegating to the deprecated v1 path: [1](#0-0) 

```
1. merkle_proof.len() == coinbase_merkle_proof.len()   (same-length guard)
2. compute_root_from_merkle_proof(coinbase_tx_id, 0, coinbase_merkle_proof) == merkle_root
3. compute_root_from_merkle_proof(tx_id, tx_index, merkle_proof) == merkle_root
```

The contract stores only block headers, never full blocks: [2](#0-1) 

Because the contract has no access to the actual coinbase transaction, it cannot verify that the caller-supplied `coinbase_tx_id` is the real coinbase. The design assumption — that a valid coinbase proof forces the proof depth to equal the full tree depth, thereby preventing internal-node substitution — is broken the moment the attacker also supplies a fake `coinbase_tx_id` that is itself an internal node.

`compute_root_from_merkle_proof` in `merkle-tools/src/lib.rs` is a pure path-traversal function: [3](#0-2) 

It accepts any starting hash and any proof length; it has no notion of "this must be a leaf." Consequently, any internal node at depth D with a proof of length D will compute to the correct root.

---

### Impact Explanation

Any downstream NEAR contract that gates an action on `verify_transaction_inclusion_v2` returning `true` (e.g., releasing bridged funds, minting wrapped tokens, recording a cross-chain event) can be deceived into accepting a forged proof. The attacker proves inclusion of a Bitcoin transaction that was never broadcast or confirmed, triggering unauthorized downstream state changes.

---

### Likelihood Explanation

**High.** The attack requires:
- Knowledge of any real mainchain block (fully public).
- Computation of its Merkle tree from public transaction data (trivial).
- A single unprivileged NEAR call to `verify_transaction_inclusion_v2` (no role, no stake, no deposit beyond gas).

The function carries only `#[pause]`, meaning it is open to every NEAR account when the contract is live. [4](#0-3) 

---

### Recommendation

The contract cannot independently verify the coinbase transaction hash because it stores only headers. The mitigation options are:

1. **Require the caller to supply the raw coinbase transaction bytes** and verify `sha256d(coinbase_bytes) == coinbase_tx_id`. This is the only way to confirm the hash is a leaf, not an internal node.
2. **Require the coinbase proof length to equal a minimum depth derived from the block's known transaction count** — but this requires storing additional per-block metadata.
3. **Document that `verify_transaction_inclusion_v2` does not fully close the 64-byte forgery window** and warn downstream integrators not to rely on it as a sole authorization gate.

---

### Proof of Concept

Take any mainchain block B with N ≥ 2 transactions and Merkle root R.

Let:
- **C** = left child of the Merkle root (internal node, position 0 at depth 1)
- **T** = right child of the Merkle root (internal node, position 1 at depth 1)

Construct the call:

```
coinbase_tx_id        = C
coinbase_merkle_proof = [T]          // length 1; position 0 is even → hash(C, T) = R ✓
tx_id                 = T
tx_index              = 1
merkle_proof          = [C]          // length 1; position 1 is odd  → hash(C, T) = R ✓
tx_block_blockhash    = B (real mainchain block)
confirmations         = 1
```

Trace through `compute_root_from_merkle_proof` for the coinbase check: [5](#0-4) 

- `current_position = 0` (even) → `current_hash = hash(C, T) = R` ✓

For the tx check (`tx_index = 1`, odd):
- `current_hash = hash(C, T) = R` ✓

Same-length requirement: `1 == 1` ✓  
Mainchain check: block B is real ✓  
Confirmations: satisfied ✓

`verify_transaction_inclusion_v2` returns **`true`**, asserting that T — an internal Merkle node, not a real transaction — is confirmed in block B. The broken invariant is identical in class to the external report: only a subset of the proof fields (the root match) is validated, while the field that anchors the proof to a real on-chain object (`coinbase_tx_id` = actual coinbase) is never checked.

### Citations

**File:** contract/src/lib.rs (L96-118)
```rust
pub struct BtcLightClient {
    // A pair of lookup maps that allows to find header by height and height by header
    mainchain_height_to_header: LookupMap<u64, H256>,
    mainchain_header_to_height: LookupMap<H256, u64>,

    // Block with the highest chainWork, i.e., blockchain tip, you can find latest height inside of it
    mainchain_tip_blockhash: H256,

    // The oldest block in main chain we store
    mainchain_initial_blockhash: H256,

    // Mapping of block hashes to block headers (ALL ever submitted, i.e., incl. forks)
    headers_pool: LookupMap<H256, ExtendedHeader>,

    // If we should run all the block checks or not
    skip_pow_verification: bool,

    // GC threshold - how many blocks we would like to store in memory, and GC the older ones
    gc_threshold: u64,

    // Network type Mainnet/Testnet
    network: Network,
}
```

**File:** contract/src/lib.rs (L346-369)
```rust
    #[pause]
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
