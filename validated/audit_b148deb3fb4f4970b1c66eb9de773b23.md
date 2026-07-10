### Title
Coinbase Depth-Binding Bypass in `verify_transaction_inclusion_v2` Allows 64-Byte Transaction Forgery — (`contract/src/lib.rs`)

---

### Summary

`verify_transaction_inclusion_v2` only enforces that `merkle_proof.len() == coinbase_merkle_proof.len()` and that the coinbase proof reconstructs the Merkle root. It does **not** validate either proof length against the block's actual tree depth, nor does it verify that `coinbase_tx_id` is a genuine leaf-level transaction. An attacker can present an internal Merkle node as `coinbase_tx_id` with a shortened proof that still reconstructs the root, completely bypassing the depth-binding protection the v2 function was designed to provide.

---

### Finding Description

`verify_transaction_inclusion_v2` performs two checks before delegating to `verify_transaction_inclusion`:

1. **Length parity**: `args.merkle_proof.len() == args.coinbase_merkle_proof.len()`
2. **Coinbase root reconstruction**: `compute_root_from_merkle_proof(coinbase_tx_id, 0, &coinbase_merkle_proof) == merkle_root` [1](#0-0) 

Neither check validates that the proof lengths equal `ceil(log2(tx_count))` — the actual tree depth — nor that `coinbase_tx_id` is the real coinbase at leaf level.

`compute_root_from_merkle_proof` is a pure iterative hash-chain function with no depth awareness: [2](#0-1) 

For a block with 4 transactions `[tx0, tx1, tx2, tx3]`, the tree is:
```
root = H(n01, n23)
  n01 = H(tx0, tx1)   ← internal node at depth 1
  n23 = H(tx2, tx3)
```

An attacker can set:
- `coinbase_tx_id = n01` (an internal node, not a leaf)
- `coinbase_merkle_proof = [n23]` (length 1, not the real depth of 2)
- `merkle_proof = [n23]` (length 1, satisfying the parity check)
- `tx_id = n01`, `tx_index = 0`

The coinbase check evaluates: `compute_root_from_merkle_proof(n01, 0, [n23]) = H(n01, n23) = root` ✓

The tx inclusion check then evaluates identically and also returns `true`. The function returns `true` for a `tx_id` that is an internal node, not a real leaf transaction.

The 64-byte forgery attack then proceeds: the attacker's "transaction" is the 64-byte concatenation `tx0 || tx1`, whose `double_sha256` equals `n01`. The contract accepts this as a proven transaction. [3](#0-2) 

---

### Impact Explanation

Any consumer of `verify_transaction_inclusion_v2` that relies on it to confirm a Bitcoin transaction is included in a block can be deceived into accepting a forged 64-byte "transaction." This is the exact attack the v2 function was introduced to prevent (per the inline doc comment referencing the BitMEX 64-byte transaction forgery post). [4](#0-3) 

Concrete impact: a bridge, DEX, or custody contract that calls `verify_transaction_inclusion_v2` to confirm a BTC deposit/withdrawal can be tricked into crediting funds for a transaction that was never broadcast or confirmed on Bitcoin.

---

### Likelihood Explanation

- No privileged role is required. `verify_transaction_inclusion_v2` is a public, unpermissioned `#[pause]`-gated view-like call.
- The attacker only needs a block that is already in the light client's mainchain (any confirmed block works).
- The forged proof is trivially constructable from publicly available block data.
- The only prerequisite is that the target block has at least 2 transactions (so internal nodes exist).

---

### Recommendation

Add an explicit depth check. The coinbase proof length must equal `ceil(log2(tx_count))`. Since the contract does not store `tx_count`, the minimum viable fix is to require that `coinbase_tx_id` is at position 0 **and** that the proof length is consistent with the coinbase being a leaf — i.e., that no shorter proof for any internal node at position 0 can also reconstruct the root. The most robust fix is to pass the block's transaction count in `ProofArgsV2` and assert:

```
coinbase_merkle_proof.len() == ceil(log2(tx_count))
merkle_proof.len()          == ceil(log2(tx_count))
``` [5](#0-4) 

Alternatively, require that `coinbase_tx_id` is fetched from a trusted on-chain source rather than supplied by the caller.

---

### Proof of Concept

Given a block with 4 transactions and `merkle_root = H(H(tx0,tx1), H(tx2,tx3))`:

```rust
// n01 = double_sha256(tx0 || tx1)  — internal node at depth 1
// n23 = double_sha256(tx2 || tx3)  — internal node at depth 1
// root = double_sha256(n01 || n23)

let args = ProofArgsV2 {
    tx_id:                  n01,   // hash of forged 64-byte "transaction"
    tx_block_blockhash:     block_hash,
    tx_index:               0,
    merkle_proof:           vec![n23],   // length 1, not the real depth 2
    coinbase_tx_id:         n01,         // internal node presented as coinbase
    coinbase_merkle_proof:  vec![n23],   // length 1 == merkle_proof.len() ✓
    confirmations:          1,
};
// verify_transaction_inclusion_v2(args) → true
```

Step-by-step:
1. Length parity: `1 == 1` ✓ [6](#0-5) 
2. Coinbase root: `compute_root_from_merkle_proof(n01, 0, [n23]) = H(n01,n23) = root` ✓ [7](#0-6) 
3. Tx inclusion: `compute_root_from_merkle_proof(n01, 0, [n23]) = root` ✓ [8](#0-7) 
4. Returns `true` — the 64-byte forged transaction `tx0 || tx1` is accepted as proven.

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

**File:** contract/src/lib.rs (L325-347)
```rust
    /// Verifies that a transaction is included in a block at a given block height,
    /// with an additional coinbase merkle proof validation.
    /// This is needed to mitigate the 64-byte transaction Merkle proof forgery vulnerability:
    /// https://www.bitmex.com/blog/64-Byte-Transactions
    ///
    /// @param tx_id transaction identifier
    /// @param tx_block_blockhash block hash at which transaction is supposedly included
    /// @param tx_index index of transaction in the block's tx merkle tree
    /// @param merkle_proof merkle tree path (concatenated LE sha256 hashes) (does not contain initial transaction_hash and merkle_root)
    /// @param coinbase_tx_id coinbase transaction hash
    /// @param coinbase_merkle_proof merkle proof for the coinbase transaction (must have the same length as merkle_proof)
    /// @param confirmations how many confirmed blocks we want to have before the transaction is valid
    /// @return True if tx_id is at the claimed position in the block at the given blockhash, False otherwise
    ///
    /// # Panics
    /// - If `merkle_proof` and `coinbase_merkle_proof` have different lengths
    /// - If `tx_block_blockhash` is not found in the headers pool
    /// - If coinbase merkle proof does not match the block's merkle root
    /// - If the required number of confirmations exceeds the number of stored blocks
    /// - If the block does not belong to the current main chain
    /// - If there are not enough confirmed blocks
    #[pause]
    pub fn verify_transaction_inclusion_v2(&self, #[serializer(borsh)] args: ProofArgsV2) -> bool {
```

**File:** contract/src/lib.rs (L348-365)
```rust
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

**File:** merkle-tools/src/lib.rs (L54-60)
```rust
fn compute_hash(first_tx_hash: &H256, second_tx_hash: &H256) -> H256 {
    let mut concat_inputs = Vec::with_capacity(64);
    concat_inputs.extend(first_tx_hash.0);
    concat_inputs.extend(second_tx_hash.0);

    double_sha256(&concat_inputs)
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
