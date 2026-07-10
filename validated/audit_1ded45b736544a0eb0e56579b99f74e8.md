### Title
Unverified `coinbase_tx_id` Bypasses 64-Byte Transaction Merkle Proof Forgery Mitigation — (`contract/src/lib.rs`, `btc-types/src/contract_args.rs`)

---

### Summary

`verify_transaction_inclusion_v2` was introduced to mitigate the 64-byte transaction Merkle proof forgery vulnerability. Its mitigation relies on requiring a valid coinbase Merkle proof of the same depth as the transaction proof. However, the `coinbase_tx_id` field in `ProofArgsV2` is entirely user-supplied and is never verified against the actual coinbase transaction of the block. An unprivileged NEAR caller can supply a fake `coinbase_tx_id` that is itself an internal Merkle tree node, satisfying the coinbase proof check while simultaneously forging a proof for a non-existent transaction. The function returns `true` for a transaction that was never included in any Bitcoin block.

---

### Finding Description

The deprecated `verify_transaction_inclusion` carries an explicit warning:

> This function may return `true` if the provided `tx_id` is a hash of an internal node in the Merkle tree rather than a valid transaction hash. [1](#0-0) 

`verify_transaction_inclusion_v2` was created to fix this by requiring a coinbase proof of equal depth: [2](#0-1) 

The only structural guard added is:

```rust
require!(
    args.merkle_proof.len() == args.coinbase_merkle_proof.len(),
    "Coinbase merkle proof and transaction merkle proof should have the same length"
);
``` [3](#0-2) 

Then the coinbase proof is validated as:

```rust
require!(
    merkle_tools::compute_root_from_merkle_proof(
        args.coinbase_tx_id.clone(),
        0usize,
        &args.coinbase_merkle_proof,
    ) == header.block_header.merkle_root,
    "Incorrect coinbase merkle proof"
);
``` [4](#0-3) 

`coinbase_tx_id` is a plain `H256` field in `ProofArgsV2` with no binding to the block's actual coinbase transaction: [5](#0-4) 

The `compute_root_from_merkle_proof` function is a pure hash-chain computation with no knowledge of what constitutes a "real" leaf vs. an internal node: [6](#0-5) 

**The broken invariant**: the mitigation assumes `coinbase_tx_id` is the real coinbase transaction hash (a genuine 32-byte leaf). Because the contract never enforces this, an attacker can supply an internal Merkle tree node as `coinbase_tx_id`, satisfying the coinbase proof check while the "transaction" proof is also forged using another internal node.

---

### Impact Explanation

Any downstream NEAR contract that calls `verify_transaction_inclusion_v2` and acts on a `true` result (e.g., releasing bridged funds, crediting a deposit, unlocking an asset) can be deceived into accepting a Bitcoin transaction that was never broadcast or confirmed. The proof-verification result — the sole security guarantee of the light client — is corrupted. This is a proof-verification forgery with direct financial impact on any consumer of the API.

---

### Likelihood Explanation

The attack requires only:
1. Any real mainchain Bitcoin block with ≥ 2 transactions (trivially available).
2. Off-chain computation of internal Merkle tree nodes (straightforward arithmetic).
3. A single unprivileged call to `verify_transaction_inclusion_v2` on NEAR.

No privileged role, leaked key, or social engineering is needed. The function is public and `#[pause]`-gated only, meaning it is reachable by any NEAR account when the contract is live.

---

### Recommendation

The contract must bind `coinbase_tx_id` to the block's actual coinbase transaction. Two concrete options:

1. **Store the coinbase hash at submission time**: When `submit_blocks` ingests a header, require the relayer to also submit the coinbase transaction hash and store it in `ExtendedHeader`. During `verify_transaction_inclusion_v2`, assert `args.coinbase_tx_id == stored_coinbase_hash`.

2. **Reject 64-byte-sized inputs**: Require that `coinbase_tx_id` and `tx_id` cannot be the hash of a 64-byte preimage by enforcing that the proof depth implies a tree with a number of leaves inconsistent with an internal-node attack. (This is harder to implement correctly and option 1 is preferred.)

The equal-length check alone is insufficient without anchoring `coinbase_tx_id` to a ground-truth value the contract controls.

---

### Proof of Concept

Consider a real mainchain block with 4 transactions `T1, T2, T3, T4` and Merkle root `R`:

```
Level 0 (leaves): T1(pos=0), T2(pos=1), T3(pos=2), T4(pos=3)
Level 1:          N12 = hash(T1,T2)(pos=0),  N34 = hash(T3,T4)(pos=1)
Level 2 (root):   R   = hash(N12, N34)
```

The attacker constructs `ProofArgsV2` as follows:

```
coinbase_tx_id       = T1 ++ T2   // 64-byte internal-node preimage, NOT a real tx
coinbase_merkle_proof = [N34]      // length 1
tx_id                = T3 ++ T4   // 64-byte internal-node preimage, NOT a real tx
merkle_proof         = [N12]      // length 1  (same length ✓)
tx_index             = 1
tx_block_blockhash   = <any real mainchain block hash with ≥2 txs>
confirmations        = 1
```

**Coinbase proof check** (inside `verify_transaction_inclusion_v2`):
```
compute_root_from_merkle_proof(T1||T2, 0, [N34])
  = hash(hash(T1||T2), N34)
  = hash(N12, N34)
  = R  ✓  (passes require!)
```

**Transaction proof check** (inside `verify_transaction_inclusion`):
```
compute_root_from_merkle_proof(T3||T4, 1, [N12])
  = hash(N12, hash(T3||T4))
  = hash(N12, N34)
  = R  ✓  (returns true!)
```

`verify_transaction_inclusion_v2` returns `true` for the fake transaction `T3 ++ T4`, which was never a real Bitcoin transaction. Both proof lengths are 1, satisfying the only structural guard. The block is a real mainchain block, satisfying the confirmation and mainchain checks.

### Citations

**File:** contract/src/lib.rs (L276-280)
```rust
    ///
    /// # Warning
    /// This function may return `true` if the provided `tx_id` is a hash of an internal node in the Merkle tree rather than a valid transaction hash.
    /// We assume that validation of whether the `tx_id` corresponds to a valid transaction hash is performed at a higher level of verification.
    ///
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
