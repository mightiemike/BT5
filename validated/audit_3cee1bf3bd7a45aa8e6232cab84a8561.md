### Title
Missing `tx_index` Upper-Bound Validation Enables Merkle Position Aliasing in SPV Proof Verification — (File: `merkle-tools/src/lib.rs`, `contract/src/lib.rs`)

---

### Summary

`verify_transaction_inclusion` and `verify_transaction_inclusion_v2` validate that `merkle_proof` is non-empty but never check that `tx_index < 2^merkle_proof.len()`. Because `compute_root_from_merkle_proof` uses only `position % 2` and `position /= 2` at each level, any index of the form `k · 2^L` (where L = proof length) produces the **identical left/right traversal** as index 0. An unprivileged NEAR caller can therefore present the coinbase transaction (index 0) as residing at index `2^L`, and the proof verifies correctly, returning `true` for a false positional claim.

---

### Finding Description

`compute_root_from_merkle_proof` in `merkle-tools/src/lib.rs` drives left/right selection purely by `current_position % 2`, then halves the position:

```rust
for proof_hash in merkle_proof {
    if current_position % 2 == 0 {
        current_hash = compute_hash(&current_hash, proof_hash);
    } else {
        current_hash = compute_hash(proof_hash, &current_hash);
    }
    current_position /= 2;
}
``` [1](#0-0) 

For a proof of length L, starting with `current_position = 2^L`:

| Level | position | `% 2` | decision |
|-------|----------|--------|----------|
| 0 | 2^L | 0 | left |
| 1 | 2^(L-1) | 0 | left |
| … | … | 0 | left |
| L-1 | 2 | 0 | left |

This is **identical** to starting with `current_position = 0` (all-left). The computed root is therefore the same for `tx_index = 0` and `tx_index = 2^L`.

In `verify_transaction_inclusion`, the only guards on `tx_index` are:

```rust
require!(!args.merkle_proof.is_empty(), "Merkle proof is empty");

merkle_tools::compute_root_from_merkle_proof(
    args.tx_id,
    usize::try_from(args.tx_index).unwrap(),
    &args.merkle_proof,
) == header.block_header.merkle_root
``` [2](#0-1) 

There is no check that `args.tx_index < (1u64 << args.merkle_proof.len())`. The analog to the external report is exact: one bound is checked (`merkle_proof.is_empty()`), but the derived relationship between `tx_index` and proof length — which constrains the valid index range — is never validated.

`verify_transaction_inclusion_v2` inherits the same gap because it converts `ProofArgsV2` to `ProofArgs` preserving `tx_index` unchanged and then delegates:

```rust
#[allow(deprecated)]
self.verify_transaction_inclusion(args.into())
``` [3](#0-2) 

```rust
impl From<ProofArgsV2> for ProofArgs {
    fn from(args: ProofArgsV2) -> Self {
        Self {
            tx_index: args.tx_index,
            ...
        }
    }
}
``` [4](#0-3) 

---

### Impact Explanation

`verify_transaction_inclusion` and `verify_transaction_inclusion_v2` are the primary SPV API consumed by downstream NEAR contracts. When the function returns `true`, the caller is told "transaction `tx_id` is at position `tx_index` in block `tx_block_blockhash`." Because `tx_index = 2^L` aliases to `tx_index = 0`, an attacker can:

1. Supply the **coinbase transaction** (always at index 0) as `tx_id`.
2. Claim `tx_index = 2^L` (where L = proof length).
3. Receive `true` — a false attestation that the coinbase is at a non-zero position.

Consumer contracts that gate on `tx_index != 0` to exclude coinbase transactions (a common pattern for preventing coinbase-based double-spend or replay attacks) would accept the coinbase as a regular transaction. The proof result returned by the contract is corrupted: it asserts a positional fact that is false.

---

### Likelihood Explanation

The attack requires only public Bitcoin blockchain data: the coinbase transaction hash and its Merkle proof, both freely available from any Bitcoin full node or block explorer. No privileged role, private key, or social engineering is needed. Any unprivileged NEAR caller can invoke `verify_transaction_inclusion_v2` with the aliased index. Likelihood is **high**.

---

### Recommendation

Add an explicit upper-bound check on `tx_index` before invoking `compute_root_from_merkle_proof`:

```rust
require!(
    args.merkle_proof.len() < 64
        && args.tx_index < (1u64 << args.merkle_proof.len()),
    "tx_index exceeds the maximum leaf index for the given proof length"
);
```

This mirrors the `maxMint` fix in the external report: after validating the input size (`merkle_proof` non-empty), also validate the derived bound (`tx_index` within the range implied by proof depth).

---

### Proof of Concept

1. Obtain a Bitcoin block B with coinbase transaction C and Merkle proof P of length L (e.g., L = 12 for a block with ~4096 transactions).
2. Call `verify_transaction_inclusion_v2` on the NEAR contract with:
   - `tx_id = C`
   - `tx_index = 2^L` (e.g., 4096)
   - `merkle_proof = P`
   - `coinbase_tx_id = C`
   - `coinbase_merkle_proof = P` (same proof; length equality check passes)
   - `tx_block_blockhash = B`
   - `confirmations = 1`
3. The coinbase guard in `verify_transaction_inclusion_v2` computes `compute_root_from_merkle_proof(C, 0, P)` = merkle_root ✓.
4. The main check computes `compute_root_from_merkle_proof(C, 2^L, P)` — identical traversal, same result = merkle_root ✓.
5. The function returns `true`, falsely attesting that coinbase transaction C is at index `2^L` (not index 0).
6. A consumer contract checking `tx_index != 0` accepts C as a non-coinbase transaction. [5](#0-4) [6](#0-5)

### Citations

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

**File:** btc-types/src/contract_args.rs (L38-47)
```rust
impl From<ProofArgsV2> for ProofArgs {
    fn from(args: ProofArgsV2) -> Self {
        Self {
            tx_id: args.tx_id,
            tx_block_blockhash: args.tx_block_blockhash,
            tx_index: args.tx_index,
            merkle_proof: args.merkle_proof,
            confirmations: args.confirmations,
        }
    }
```
