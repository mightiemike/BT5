### Title
`verify_transaction_inclusion_v2` Coinbase Proof Bypass via Unconstrained `coinbase_tx_id` — (`contract/src/lib.rs`)

---

### Summary

`verify_transaction_inclusion_v2` was introduced to mitigate the 64-byte Merkle proof forgery attack by requiring a coinbase proof at position 0 with the same depth as the transaction proof. However, the function accepts a fully user-controlled `coinbase_tx_id` without verifying it is the actual coinbase transaction hash. An unprivileged NEAR caller can supply an internal Merkle tree node as both `coinbase_tx_id` and `tx_id`, satisfying every check and causing the function to return `true` for a transaction that does not exist.

---

### Finding Description

The vulnerability class from H-03 is **user-controlled inputs that bypass an intended restriction because the function performs no binding validation between the caller-supplied parameter and the protocol-enforced invariant**. The exact analog exists here.

`verify_transaction_inclusion_v2` performs three checks:

1. `merkle_proof.len() == coinbase_merkle_proof.len()` — proof depths must match.
2. `compute_root_from_merkle_proof(coinbase_tx_id, 0, coinbase_merkle_proof) == merkle_root` — the coinbase proof must reach the stored root.
3. Delegates to the deprecated `verify_transaction_inclusion`, which recomputes the root from `tx_id`, `tx_index`, and `merkle_proof` and compares it to the stored root. [1](#0-0) 

The critical missing check: **nowhere is `coinbase_tx_id` required to equal the actual coinbase transaction of the block**. The contract stores only headers, so it has no record of the real coinbase txid. The caller has complete freedom to supply any 32-byte value as `coinbase_tx_id`. [2](#0-1) 

`compute_root_from_merkle_proof` is fully deterministic: given the same `(hash, position, proof)` triple it always returns the same root. [3](#0-2) 

---

### Impact Explanation

Consider a real block whose Merkle tree contains four transactions T1, T2, T3, T4:

```
Root = H(H12 ‖ H34)
       /              \
H12 = H(T1‖T2)    H34 = H(T3‖T4)
```

An attacker sets:

| Field | Value |
|---|---|
| `coinbase_tx_id` | H12 (internal node) |
| `coinbase_merkle_proof` | `[H34]` |
| `tx_id` | H12 (same internal node) |
| `tx_index` | 0 |
| `merkle_proof` | `[H34]` |
| `tx_block_blockhash` | hash of the real block |

Check 1 passes: both proofs have length 1.
Check 2 passes: `H(H12 ‖ H34) == Root` ✓
Check 3 passes: `H(H12 ‖ H34) == Root` ✓

`verify_transaction_inclusion_v2` returns `true` for H12, which is **not a transaction**. Any downstream bridge or application that gates fund release on this return value can be deceived into releasing assets for a Bitcoin payment that never occurred. This breaks the core security guarantee of the light client. [4](#0-3) 

---

### Likelihood Explanation

The attack requires no privileged role. `verify_transaction_inclusion_v2` is a public, pausable method callable by any NEAR account. [5](#0-4) 

The attacker only needs:
- A real block already accepted into the mainchain (supplied by the honest relayer).
- The block's transaction list (public on-chain Bitcoin data) to compute H12 and H34.
- A single NEAR RPC call with crafted `ProofArgsV2`. [6](#0-5) 

No hash preimage computation, no key material, and no privileged access is needed.

---

### Recommendation

The contract stores only block headers and therefore cannot independently look up the real coinbase txid. Two complementary mitigations:

1. **Require the caller to supply the raw coinbase transaction bytes.** Verify `double_sha256(raw_coinbase) == coinbase_tx_id` and that the first input's outpoint is the null outpoint (all-zero txid, index `0xFFFFFFFF`), which is the Bitcoin protocol invariant for coinbase inputs. This binds `coinbase_tx_id` to a real coinbase transaction without storing full blocks.

2. **Reject proofs where `tx_id == coinbase_tx_id`.** This is a cheap guard that blocks the simplest form of the attack (using the same internal node for both fields), though it does not cover all variants. [7](#0-6) 

---

### Proof of Concept

Given a block at height H already in the mainchain with four transactions T1, T2, T3, T4:

```rust
// Attacker computes off-chain:
let h12 = double_sha256(&[T1.as_bytes(), T2.as_bytes()].concat()); // internal node
let h34 = double_sha256(&[T3.as_bytes(), T4.as_bytes()].concat()); // internal node

// Attacker calls verify_transaction_inclusion_v2 with:
let args = ProofArgsV2 {
    tx_id:                  h12.clone(),   // internal node, NOT a real transaction
    tx_block_blockhash:     real_block_hash,
    tx_index:               0,
    merkle_proof:           vec![h34.clone()],
    coinbase_tx_id:         h12.clone(),   // same internal node, NOT the real coinbase
    coinbase_merkle_proof:  vec![h34.clone()],
    confirmations:          1,
};
// Returns: true
```

All three guards in `verify_transaction_inclusion_v2` pass because both the coinbase proof and the transaction proof use identical inputs that legitimately reconstruct the stored `merkle_root`. The function returns `true` for a hash that corresponds to no Bitcoin transaction. [1](#0-0) [3](#0-2)

### Citations

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

**File:** btc-types/src/contract_args.rs (L26-36)
```rust
#[near(serializers = [borsh, json])]
#[derive(Clone, Debug)]
pub struct ProofArgsV2 {
    pub tx_id: H256,
    pub tx_block_blockhash: H256,
    pub tx_index: u64,
    pub merkle_proof: Vec<H256>,
    pub coinbase_tx_id: H256,
    pub coinbase_merkle_proof: Vec<H256>,
    pub confirmations: u64,
}
```

**File:** btc-types/src/hash.rs (L110-123)
```rust
pub fn double_sha256(input: &[u8]) -> H256 {
    #[cfg(target_arch = "wasm32")]
    {
        H256(
            near_sdk::env::sha256(&near_sdk::env::sha256(input))
                .try_into()
                .unwrap(),
        )
    }
    #[cfg(not(target_arch = "wasm32"))]
    {
        use sha2::{Digest, Sha256};
        H256(Sha256::digest(Sha256::digest(input)).into())
    }
```
