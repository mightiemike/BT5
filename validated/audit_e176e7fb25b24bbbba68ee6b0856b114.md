### Title
Unconditional Empty-Proof Guard Permanently Blocks SPV Verification for Single-Transaction Blocks — (`contract/src/lib.rs`)

---

### Summary

`verify_transaction_inclusion` (and by extension `verify_transaction_inclusion_v2`, which delegates to it) unconditionally panics when `merkle_proof` is empty. For a Bitcoin block that contains exactly one transaction, the mathematically correct Merkle proof **is** the empty vector — the transaction hash equals the Merkle root and no sibling nodes exist. Any unprivileged NEAR caller supplying a structurally valid proof for such a block will receive a hard panic instead of the correct `true` result, permanently breaking SPV verification for that class of blocks.

---

### Finding Description

`verify_transaction_inclusion` accepts a caller-supplied `ProofArgs` struct and, before computing the Merkle root, executes:

```rust
require!(!args.merkle_proof.is_empty(), "Merkle proof is empty");
``` [1](#0-0) 

This guard is applied unconditionally, regardless of the block's transaction count. The underlying `compute_root_from_merkle_proof` in `merkle-tools` is already correct for the empty case — when `merkle_proof` is empty it returns `transaction_hash` unchanged:

```rust
for proof_hash in merkle_proof {   // loop body never executes
    ...
}
current_hash   // == transaction_hash
``` [2](#0-1) 

So for a single-transaction block where `merkle_root == tx_id`, the expression on lines 318-322 would correctly evaluate to `true` — but the `require!` on line 315 panics before it is ever reached.

`verify_transaction_inclusion_v2` does not fix this. It enforces `merkle_proof.len() == coinbase_merkle_proof.len()`, so for a single-tx block both vectors must be empty. The coinbase check then passes trivially (`compute_root_from_merkle_proof(coinbase_tx_id, 0, &[]) == merkle_root` when `coinbase_tx_id == merkle_root`), and the call is forwarded to the deprecated v1 function, which panics:

```rust
#[allow(deprecated)]
self.verify_transaction_inclusion(args.into())
``` [3](#0-2) 

The `ProofArgs` and `ProofArgsV2` structs are passed wholesale from the caller with no server-side construction of the proof fields: [4](#0-3) 

There is no alternative code path. Both public verification entry points share the same broken guard.

---

### Impact Explanation

The BTC light client's stated purpose is to let NEAR smart contracts verify Bitcoin transaction inclusion before releasing on-chain assets (e.g., bridge unlocks, cross-chain settlements). If the target Bitcoin transaction is the sole transaction in its block, the verification call panics unconditionally. The downstream NEAR contract receives a failed cross-contract call and cannot release the locked funds. The funds remain permanently locked with no recourse, because the contract provides no fallback verification path and the caller cannot alter which Bitcoin block their transaction was mined in.

---

### Likelihood Explanation

Single-transaction Bitcoin blocks (containing only the coinbase) occur regularly during low-fee periods, during mining pool testing, and are common in Dogecoin, Litecoin, and Zcash — all chains explicitly supported by this contract via feature flags. Any bridge or settlement contract that relies on `verify_transaction_inclusion_v2` for a transaction mined in such a block will trigger the panic. The entry point is fully unprivileged (no role required, no staking required).

---

### Recommendation

Remove the blanket empty-proof guard and instead handle the single-transaction case explicitly:

```rust
// If proof is empty, the tx must be the sole transaction (tx_id == merkle_root)
if args.merkle_proof.is_empty() {
    return args.tx_id == header.block_header.merkle_root;
}
```

Apply the same fix inside `verify_transaction_inclusion_v2` before delegating to v1, or remove the delegation and inline the corrected logic directly.

---

### Proof of Concept

1. Mine (or locate) a Bitcoin-family block `B` containing exactly one transaction with id `T`. Its `merkle_root == T`.
2. Submit `B`'s header to the contract via `submit_blocks` so it is recorded on-chain.
3. Wait for the required number of confirmations.
4. Call `verify_transaction_inclusion_v2` with:
   - `tx_id = T`
   - `tx_block_blockhash = hash(B)`
   - `tx_index = 0`
   - `merkle_proof = []`
   - `coinbase_tx_id = T`
   - `coinbase_merkle_proof = []`
   - `confirmations = 1`
5. Execution path:
   - Length check: `0 == 0` → passes. [5](#0-4) 
   - Coinbase check: `compute_root_from_merkle_proof(T, 0, &[]) == T == merkle_root` → passes. [6](#0-5) 
   - Delegates to `verify_transaction_inclusion` with `merkle_proof = []`.
   - `require!(!args.merkle_proof.is_empty(), "Merkle proof is empty")` → **panics**. [1](#0-0) 
6. The downstream contract receives a failed call; any funds gated on this verification remain locked.

### Citations

**File:** contract/src/lib.rs (L315-315)
```rust
        require!(!args.merkle_proof.is_empty(), "Merkle proof is empty");
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

**File:** contract/src/lib.rs (L367-368)
```rust
        #[allow(deprecated)]
        self.verify_transaction_inclusion(args.into())
```

**File:** merkle-tools/src/lib.rs (L42-51)
```rust
    for proof_hash in merkle_proof {
        if current_position % 2 == 0 {
            current_hash = compute_hash(&current_hash, proof_hash);
        } else {
            current_hash = compute_hash(proof_hash, &current_hash);
        }
        current_position /= 2;
    }

    current_hash
```

**File:** btc-types/src/contract_args.rs (L16-36)
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
