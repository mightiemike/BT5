### Title
Fabricated `coinbase_tx_id` Bypasses 64-Byte Forgery Mitigation in `verify_transaction_inclusion_v2` — (`contract/src/lib.rs`)

---

### Summary

`verify_transaction_inclusion_v2` only checks that `compute_root_from_merkle_proof(coinbase_tx_id, 0, coinbase_merkle_proof) == merkle_root`. It never verifies that `coinbase_tx_id` is the actual coinbase transaction hash. An unprivileged NEAR caller can supply an internal Merkle tree node as `coinbase_tx_id`, satisfy the coinbase check with a proof one level shorter than the real tree depth, and simultaneously prove that the same internal node is a "transaction" at position 0 — making the function return `true` for a non-existent transaction.

---

### Finding Description

`verify_transaction_inclusion_v2` performs three checks: [1](#0-0) 

1. `merkle_proof.len() == coinbase_merkle_proof.len()` [2](#0-1) 

2. `compute_root_from_merkle_proof(coinbase_tx_id, 0, coinbase_merkle_proof) == merkle_root` [3](#0-2) 

3. Delegates to `verify_transaction_inclusion`, which checks `compute_root_from_merkle_proof(tx_id, tx_index, merkle_proof) == merkle_root`

There is **no check** that `coinbase_tx_id` is the actual coinbase transaction. Any 32-byte value that satisfies the root equality at position 0 is accepted.

`compute_root_from_merkle_proof` is a pure algebraic function: [4](#0-3) 

For a 4-transaction block with leaf hashes `tx0, tx1, tx2, tx3`:
- `n01 = SHA256d(tx0 || tx1)` — internal node at depth 1, position 0
- `n23 = SHA256d(tx2 || tx3)` — internal node at depth 1, position 1
- `root = SHA256d(n01 || n23)`

An attacker calls `verify_transaction_inclusion_v2` with:

| Field | Value |
|---|---|
| `tx_id` | `n01` (internal node, not a real txid) |
| `tx_index` | `0` |
| `merkle_proof` | `[n23]` (length 1) |
| `coinbase_tx_id` | `n01` (same internal node) |
| `coinbase_merkle_proof` | `[n23]` (length 1) |
| `tx_block_blockhash` | any real canonical block hash |

- Check 1: `1 == 1` ✓
- Check 2: `compute_root_from_merkle_proof(n01, 0, [n23])` = `SHA256d(n01 || n23)` = `root` ✓
- Check 3: identical computation, also equals `root` ✓

The function returns `true`. No cryptographic assumption is required — `n01` and `n23` are computed directly from the public Merkle tree of any real block.

---

### Impact Explanation

The function returns `true` for `tx_id = n01`, which is an internal Merkle tree node, not a real transaction. Any downstream system (e.g., a bridge contract) that calls `verify_transaction_inclusion_v2` to gate fund releases can be deceived into accepting a false inclusion proof for a fabricated transaction ID. The 64-byte forgery mitigation — the entire purpose of `v2` over the deprecated `v1` — is completely nullified. [5](#0-4) 

The deprecation notice on `v1` explicitly states `v2` was introduced to prevent this class of attack. The bypass renders `v2` no safer than `v1`.

---

### Likelihood Explanation

- The function is public, callable by any NEAR account with no role restriction beyond the `#[pause]` gate.
- The attack requires only knowledge of a real block's Merkle tree (publicly available on-chain).
- No privileged key, DAO role, or cryptographic break is needed.
- The computation is trivial: hash two known leaf hashes, use the result as both `tx_id` and `coinbase_tx_id`.

---

### Recommendation

Verify that `coinbase_tx_id` is the actual coinbase transaction by requiring the caller to supply the raw coinbase transaction bytes, then computing `coinbase_tx_id = coinbase_tx.compute_txid()` on-chain before running the Merkle proof check. This is exactly how `check_aux` in `dogecoin.rs` handles it: [6](#0-5) 

Apply the same pattern to `verify_transaction_inclusion_v2`: accept raw coinbase transaction bytes, compute the txid on-chain, and use that computed value in the Merkle proof check. This prevents any caller from substituting an internal node for the real coinbase txid.

---

### Proof of Concept

Given a real canonical block with 4 transactions and known leaf hashes `tx0, tx1, tx2, tx3`:

```
n01  = SHA256d(tx0 || tx1)   // internal node, depth 1, position 0
n23  = SHA256d(tx2 || tx3)   // internal node, depth 1, position 1
root = SHA256d(n01 || n23)   // merkle root (matches block header)
```

Call `verify_transaction_inclusion_v2` with:
```
tx_id                = n01
tx_index             = 0
merkle_proof         = [n23]          // length 1
coinbase_tx_id       = n01            // same internal node
coinbase_merkle_proof = [n23]         // length 1
tx_block_blockhash   = <real block>
confirmations        = 1
```

All three checks pass. The function returns `true` for `tx_id = n01`, which is not a real transaction. The 64-byte forgery mitigation is bypassed without any cryptographic assumption. [7](#0-6)

### Citations

**File:** contract/src/lib.rs (L263-287)
```rust
    /// Verifies that a transaction is included in a block at a given block height
    ///
    /// # Deprecated
    /// Use [`verify_transaction_inclusion_v2`] instead, which includes coinbase merkle proof validation
    /// to mitigate the 64-byte transaction Merkle proof forgery vulnerability:
    /// https://www.bitmex.com/blog/64-Byte-Transactions
    ///
    /// @param `tx_id` transaction identifier
    /// @param `tx_block_blockhash` block hash at which transacton is supposedly included
    /// @param `tx_index` index of transaction in the block's tx merkle tree
    /// @param `merkle_proof` merkle tree path (concatenated LE sha256 hashes) (does not contain initial `transaction_hash` and `merkle_root`)
    /// @param confirmations how many confirmed blocks we want to have before the transaction is valid
    /// @return True if `tx_id` is at the claimed position in the block at the given blockhash, False otherwise
    ///
    /// # Warning
    /// This function may return `true` if the provided `tx_id` is a hash of an internal node in the Merkle tree rather than a valid transaction hash.
    /// We assume that validation of whether the `tx_id` corresponds to a valid transaction hash is performed at a higher level of verification.
    ///
    /// # Panics
    /// Multiple cases
    #[deprecated(
        since = "0.5.0",
        note = "Use `verify_transaction_inclusion_v2` instead."
    )]
    #[pause]
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

**File:** contract/src/dogecoin.rs (L84-93)
```rust
        let coinbase_tx = aux_data.get_coinbase_tx();
        let coinbase_tx_hash = coinbase_tx.compute_txid();

        require!(
            merkle_tools::compute_root_from_merkle_proof(
                H256::from(coinbase_tx_hash.to_raw_hash().to_byte_array()),
                0,
                &aux_data.merkle_proof,
            ) == aux_data.parent_block.merkle_root
        );
```
