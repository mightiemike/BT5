### Title
Coinbase Depth-Binding Bypass in `verify_transaction_inclusion_v2` — Internal Node Accepted as Coinbase, Nullifying 64-Byte Forgery Protection — (`merkle-tools/src/lib.rs`, `contract/src/lib.rs`)

---

### Summary

`verify_transaction_inclusion_v2` only checks that `merkle_proof.len() == coinbase_merkle_proof.len()` and that the supplied `coinbase_tx_id` reconstructs the block's Merkle root via `compute_root_from_merkle_proof`. It never validates that `coinbase_tx_id` is the actual leaf-level coinbase transaction, nor that either proof length equals the block's true tree depth. An unprivileged NEAR caller can supply an internal Merkle tree node as `coinbase_tx_id` with a shortened proof that still reconstructs the root, then supply the same internal node as `tx_id` with an equally shortened `merkle_proof`. Both checks pass, `verify_transaction_inclusion_v2` returns `true`, and the entire 64-byte forgery protection is nullified.

---

### Finding Description

**The intended protection model of v2:**

The coinbase transaction is always a real leaf at index 0. The design assumption is: if the caller must also supply a valid coinbase proof at the *same depth* as the target tx proof, then both proofs must reach leaf level, preventing an internal node from being presented as a transaction.

**The missing enforcement:**

`verify_transaction_inclusion_v2` enforces only two things:

1. `args.merkle_proof.len() == args.coinbase_merkle_proof.len()` [1](#0-0) 

2. `compute_root_from_merkle_proof(args.coinbase_tx_id, 0, &args.coinbase_merkle_proof) == header.block_header.merkle_root` [2](#0-1) 

Neither check validates that `coinbase_tx_id` is the actual coinbase transaction (the real leaf at index 0), nor that the proof length equals `ceil(log2(tx_count))`.

**`compute_root_from_merkle_proof` is depth-agnostic:**

The function simply iterates over whatever proof elements are supplied, hashing upward from the given starting hash. It has no concept of tree depth or leaf vs. internal node: [3](#0-2) 

**Concrete attack on a 4-transaction block:**

```
Tree:
         root
        /    \
      n01    n23
     /  \   /  \
   tx0 tx1 tx2 tx3

n01 = hash(tx0 || tx1)   ← internal node at depth 1
n23 = hash(tx2 || tx3)   ← internal node at depth 1
root = hash(n01 || n23)
```

The attacker calls `verify_transaction_inclusion_v2` with:

| Field | Value |
|---|---|
| `coinbase_tx_id` | `n01` (internal node, NOT the real coinbase tx0) |
| `coinbase_merkle_proof` | `[n23]` (length 1) |
| `tx_id` | `n01` (same internal node, presented as target tx) |
| `tx_index` | `0` |
| `merkle_proof` | `[n23]` (length 1) |
| `confirmations` | `0` |

Trace:
- **Length check**: `1 == 1` ✓
- **Coinbase check**: `compute_root_from_merkle_proof(n01, 0, [n23])` → position 0 is even → `hash(n01, n23)` = `root` ✓
- **`verify_transaction_inclusion`** (via `args.into()`): `compute_root_from_merkle_proof(n01, 0, [n23])` = `root` ✓ → returns `true`

The `From<ProofArgsV2> for ProofArgs` conversion passes `tx_id`, `tx_index`, and `merkle_proof` directly, discarding the coinbase fields entirely: [4](#0-3) 

The downstream `verify_transaction_inclusion` call then independently re-verifies the same `(n01, 0, [n23])` triple against the root — which passes for the same reason: [5](#0-4) 

---

### Impact Explanation

The v2 coinbase depth-binding protection is completely nullified. Any caller can present an internal Merkle tree node as both the `coinbase_tx_id` and the `tx_id`, with a proof shorter than the actual tree depth, and receive a `true` return value. This is the exact class of forgery that v2 was introduced to prevent. Downstream systems (e.g., bridges, payment verifiers) that rely on `verify_transaction_inclusion_v2` returning `true` only for real leaf-level transactions are exposed to the 64-byte transaction forgery attack through the v2 path.

---

### Likelihood Explanation

The call is a public `#[pause]`-gated view function with no access control beyond the pause flag. Any NEAR account can invoke it. The only prerequisite is that the target block exists in `headers_pool`, which is satisfied for any legitimately relayed block. No privileged role, key compromise, or social engineering is required. The crafted arguments are trivially computable from the block's transaction list.

---

### Recommendation

1. **Enforce that `coinbase_tx_id` is the actual coinbase**: Store the coinbase txid in the block header metadata at submission time, or require the caller to supply `tx_index = 0` for the coinbase and validate it independently.

2. **Validate proof depth against tree depth**: Require `coinbase_merkle_proof.len() == ceil(log2(tx_count))`. Since Bitcoin block headers do not contain `tx_count`, this requires either storing `tx_count` at block submission time or requiring the caller to supply it with a separate proof.

3. **Reject `coinbase_tx_id == tx_id` when `tx_index != 0`**: As a partial mitigation, if the coinbase and target tx are the same hash, the target must be at index 0.

4. **Minimum proof length**: Enforce `coinbase_merkle_proof.len() >= 1` (already done via `verify_transaction_inclusion`'s `!args.merkle_proof.is_empty()` check), but this is insufficient alone.

---

### Proof of Concept

```rust
// Given a 4-tx block with transactions [tx0, tx1, tx2, tx3]:
// n01 = hash(tx0 || tx1)
// n23 = hash(tx2 || tx3)
// root = hash(n01 || n23)

let n01 = compute_hash(&tx0, &tx1);
let n23 = compute_hash(&tx2, &tx3);

// Both proofs have length 1 (tree depth is actually 2)
let result: bool = user_account
    .view(contract.id(), "verify_transaction_inclusion_v2")
    .args_borsh(ProofArgsV2 {
        tx_id: n01.clone(),           // internal node as "target tx"
        tx_block_blockhash: block_hash,
        tx_index: 0,
        merkle_proof: vec![n23.clone()],
        coinbase_tx_id: n01.clone(),  // same internal node as "coinbase"
        coinbase_merkle_proof: vec![n23.clone()],
        confirmations: 0,
    })
    .await?
    .json()?;

assert!(result); // returns true — v2 protection bypassed
```

`compute_root_from_merkle_proof(n01, 0, [n23])` = `hash(n01, n23)` = `root` for both the coinbase check and the tx check. The length equality check passes trivially. The function returns `true` for an internal node, defeating the entire purpose of v2.

### Citations

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

**File:** contract/src/lib.rs (L348-351)
```rust
        require!(
            args.merkle_proof.len() == args.coinbase_merkle_proof.len(),
            "Coinbase merkle proof and transaction merkle proof should have the same length"
        );
```

**File:** contract/src/lib.rs (L358-365)
```rust
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
