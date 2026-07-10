### Title
`verify_transaction_inclusion_v2` Unconditionally Inherits Empty-Proof Guard from Deprecated v1, Blocking Valid Single-Transaction Block Verification - (File: `contract/src/lib.rs`)

### Summary

`verify_transaction_inclusion_v2` delegates to the deprecated `verify_transaction_inclusion` after completing its own coinbase-proof check. The deprecated function contains an unconditional `require!(!args.merkle_proof.is_empty())` guard that panics for single-transaction blocks, where an empty proof is mathematically correct. The coinbase-proof step in v2 already establishes the security invariant that the guard was designed to enforce, making the guard redundant and harmful in the v2 call path.

### Finding Description

`verify_transaction_inclusion_v2` is the security-upgraded replacement for the deprecated `verify_transaction_inclusion`. Its purpose is to add a coinbase-proof step to defeat the 64-byte Merkle forgery attack. After verifying the coinbase proof, it calls the deprecated v1 function unconditionally:

```rust
// contract/src/lib.rs  line 347-369
pub fn verify_transaction_inclusion_v2(&self, #[serializer(borsh)] args: ProofArgsV2) -> bool {
    require!(
        args.merkle_proof.len() == args.coinbase_merkle_proof.len(), ...
    );
    ...
    require!(
        merkle_tools::compute_root_from_merkle_proof(
            args.coinbase_tx_id.clone(), 0usize, &args.coinbase_merkle_proof,
        ) == header.block_header.merkle_root,
        "Incorrect coinbase merkle proof"
    );

    #[allow(deprecated)]
    self.verify_transaction_inclusion(args.into())   // ← delegates to v1
}
```

Inside the deprecated v1 function, the following guard fires unconditionally:

```rust
// contract/src/lib.rs  line 315
require!(!args.merkle_proof.is_empty(), "Merkle proof is empty");
```

For a block that contains exactly one transaction (the coinbase), the Merkle root **is** the coinbase transaction hash. The correct proof for that transaction is an empty slice. `compute_root_from_merkle_proof` handles this correctly — with an empty proof it returns the input hash unchanged:

```rust
// merkle-tools/src/lib.rs  line 34-52
pub fn compute_root_from_merkle_proof(
    transaction_hash: H256, transaction_position: usize, merkle_proof: &Vec<H256>,
) -> H256 {
    let mut current_hash = transaction_hash;
    for proof_hash in merkle_proof { ... }   // loop body never executes for empty proof
    current_hash                             // returns transaction_hash directly
}
```

The execution path for a single-transaction block in v2:

1. `merkle_proof.len() == coinbase_merkle_proof.len()` → `0 == 0` ✓ passes
2. `compute_root_from_merkle_proof(coinbase_tx_id, 0, &[]) == merkle_root` → `coinbase_tx_id == merkle_root` ✓ passes (valid single-tx block)
3. `require!(!args.merkle_proof.is_empty())` → `!true` → **panics**, blocking the call

The guard in v1 exists to prevent a trivial forgery: without it, an attacker could pass `tx_id = merkle_root` with an empty proof and claim the root itself is a transaction. In v2, the coinbase-proof step already closes this attack surface — the coinbase proof forces `coinbase_tx_id == merkle_root`, and the transaction proof must independently reproduce the same root. The empty-proof guard is therefore unnecessary in the v2 call path and becomes an obstacle for the legitimate single-transaction-block case.

### Impact Explanation

Any downstream NEAR contract or off-chain verifier that calls `verify_transaction_inclusion_v2` to verify a transaction in a single-transaction block will always receive a panic instead of a result. Because the function is `#[pause]`-gated and publicly callable, this affects every unprivileged caller. Downstream bridge contracts that gate asset releases on a `true` result from this function will be permanently unable to process claims backed by single-transaction blocks, even when those blocks are fully valid and confirmed on the Bitcoin chain.

### Likelihood Explanation

Single-transaction blocks (containing only the coinbase) occur in practice: miners sometimes produce them to collect the block subsidy quickly when the mempool is empty or fees are negligible. They are valid Bitcoin blocks and appear on mainnet. Any relayer that submits such a block to the contract will store it correctly, but no caller will ever be able to use `verify_transaction_inclusion_v2` to prove inclusion of the coinbase transaction in that block.

### Recommendation

Remove the `require!(!args.merkle_proof.is_empty())` guard from the shared path, or skip it when `verify_transaction_inclusion` is called from `verify_transaction_inclusion_v2`. The simplest fix is to inline the proof-computation logic in v2 rather than delegating to v1, and to omit the empty-proof guard there since the coinbase-proof step already provides the equivalent security guarantee:

```rust
pub fn verify_transaction_inclusion_v2(&self, args: ProofArgsV2) -> bool {
    // ... existing coinbase proof check ...

    // Compute transaction proof directly — no empty-proof guard needed here
    // because the coinbase proof already anchors the merkle root.
    compute_root_from_merkle_proof(args.tx_id, args.tx_index, &args.merkle_proof)
        == header.block_header.merkle_root
    // plus the confirmations check from v1
}
```

### Proof of Concept

1. Relayer submits a valid single-transaction Bitcoin block (only the coinbase). The contract stores it correctly via `submit_blocks`.
2. A downstream contract calls `verify_transaction_inclusion_v2` with:
   - `tx_id = coinbase_tx_id = merkle_root`
   - `tx_index = 0`
   - `merkle_proof = []`
   - `coinbase_tx_id = merkle_root`
   - `coinbase_merkle_proof = []`
   - `confirmations = 1`
3. Step 1 of v2: length check `0 == 0` passes. [1](#0-0) 
4. Step 2 of v2: `compute_root_from_merkle_proof(merkle_root, 0, &[])` returns `merkle_root`, coinbase proof check passes. [2](#0-1) 
5. Step 3: `verify_transaction_inclusion` is called. [3](#0-2) 
6. Inside v1, `require!(!args.merkle_proof.is_empty())` fires and **panics** — the call reverts. [4](#0-3) 
7. `compute_root_from_merkle_proof` with an empty proof simply returns the input hash, confirming the empty proof is mathematically valid. [5](#0-4)

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

**File:** merkle-tools/src/lib.rs (L38-51)
```rust
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
```
