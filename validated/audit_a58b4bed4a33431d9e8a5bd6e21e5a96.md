### Title
Deprecated `verify_transaction_inclusion` Remains Publicly Callable, Bypassing the Coinbase Merkle Proof Invariant Enforced by `verify_transaction_inclusion_v2` — (`contract/src/lib.rs`)

---

### Summary

`verify_transaction_inclusion` (v1) is still a live, unrestricted public entry point. `verify_transaction_inclusion_v2` was introduced specifically to close the 64-byte transaction Merkle-proof forgery vulnerability by requiring a coinbase proof. Because v1 is never removed or access-gated, any NEAR caller can invoke it directly, bypassing the coinbase-proof invariant that v2 enforces, and obtain a `true` inclusion result for a forged proof.

---

### Finding Description

The contract exposes two public functions for transaction inclusion verification:

**v1** — deprecated but still fully callable: [1](#0-0) 

```rust
#[deprecated(since = "0.5.0", note = "Use `verify_transaction_inclusion_v2` instead.")]
#[pause]
pub fn verify_transaction_inclusion(&self, #[serializer(borsh)] args: ProofArgs) -> bool {
    // ... confirmations check, main-chain check ...
    merkle_tools::compute_root_from_merkle_proof(
        args.tx_id,
        usize::try_from(args.tx_index).unwrap(),
        &args.merkle_proof,
    ) == header.block_header.merkle_root
}
```

**v2** — the secure replacement: [2](#0-1) 

```rust
#[pause]
pub fn verify_transaction_inclusion_v2(&self, #[serializer(borsh)] args: ProofArgsV2) -> bool {
    // ... length check ...
    require!(
        merkle_tools::compute_root_from_merkle_proof(
            args.coinbase_tx_id.clone(), 0usize, &args.coinbase_merkle_proof,
        ) == header.block_header.merkle_root,
        "Incorrect coinbase merkle proof"
    );
    #[allow(deprecated)]
    self.verify_transaction_inclusion(args.into())   // calls v1 internally
}
```

The coinbase-proof check exists solely to prevent the 64-byte transaction Merkle-proof forgery attack (referenced in the v2 doc comment: https://www.bitmex.com/blog/64-Byte-Transactions). v2 enforces it; v1 does not. v1 carries only a `#[pause]` guard — no role restriction, no access control — so any NEAR account can call it directly when the contract is unpaused.

The `ProofArgs` struct accepted by v1 contains no `coinbase_tx_id` or `coinbase_merkle_proof` fields: [3](#0-2) 

There is therefore no way for v1 to perform the coinbase check even if it wanted to. The invariant — *all transaction inclusion proofs must be anchored by a valid coinbase proof* — is enforced only on the v2 path.

---

### Impact Explanation

The 64-byte transaction attack allows an adversary to supply a hash of an internal Merkle-tree node as `tx_id`. Because Bitcoin's Merkle tree does not distinguish leaf nodes from internal nodes, a carefully crafted 64-byte value can be made to hash to a value that, combined with a crafted `merkle_proof`, reproduces the block's `merkle_root`. v1 will return `true` for such a proof.

Any downstream NEAR contract or off-chain application that calls `verify_transaction_inclusion` (v1) directly — rather than v2 — will accept a forged proof as valid. Given that the function is public and the contract is designed to be consumed by other contracts for SPV verification, this is a realistic consumer path. A false `true` result from the light client is the corrupted output: the canonical "this transaction is confirmed on Bitcoin" assertion becomes forgeable.

---

### Likelihood Explanation

The entry path requires no privilege: any NEAR account can call `verify_transaction_inclusion` when the contract is unpaused. The function is listed in the contract's ABI. Downstream integrators reading the ABI or older documentation may call v1 without knowing it is insecure. The `#[deprecated]` Rust attribute is a compiler hint; it does not prevent on-chain invocation. The attack is realistic for any integrator that has not explicitly migrated to v2.

---

### Recommendation

Remove `verify_transaction_inclusion` from the public ABI entirely, or gate it with a role that no external caller holds (e.g., `#[private]`). The internal call from `verify_transaction_inclusion_v2` should be refactored to call a private helper that contains the shared logic, so the coinbase-proof check cannot be circumvented by choosing the v1 entry point.

---

### Proof of Concept

1. Attacker identifies a Bitcoin block `B` with Merkle root `R`.
2. Attacker constructs a 64-byte value `N` (an internal Merkle-tree node of `B`) and a `merkle_proof` path such that `compute_root_from_merkle_proof(N, index, proof) == R`.
3. Attacker calls `verify_transaction_inclusion` directly with `tx_id = N`, `tx_block_blockhash = hash(B)`, `tx_index = index`, `merkle_proof = proof`, `confirmations = 1`.
4. v1 checks that `hash(B)` is on the main chain ✓, that confirmations are satisfied ✓, and that the computed root equals `R` ✓ — returns `true`.
5. The same call to `verify_transaction_inclusion_v2` would fail at the coinbase-proof `require!`, because the attacker cannot produce a valid coinbase proof that is consistent with the forged internal-node path.

The bypass is reachable at: [4](#0-3)

### Citations

**File:** contract/src/lib.rs (L283-323)
```rust
    #[deprecated(
        since = "0.5.0",
        note = "Use `verify_transaction_inclusion_v2` instead."
    )]
    #[pause]
    pub fn verify_transaction_inclusion(&self, #[serializer(borsh)] args: ProofArgs) -> bool {
        require!(
            args.confirmations <= self.gc_threshold,
            "The required number of confirmations exceeds the number of blocks stored in memory"
        );

        let heaviest_block_header = self
            .headers_pool
            .get(&self.mainchain_tip_blockhash)
            .unwrap_or_else(|| env::panic_str(ERR_KEY_NOT_EXIST));
        let target_block_height = self
            .mainchain_header_to_height
            .get(&args.tx_block_blockhash)
            .unwrap_or_else(|| env::panic_str("block does not belong to the current main chain"));

        // Check requested confirmations. No need to compute proof if insufficient confirmations.
        require!(
            (heaviest_block_header.block_height).saturating_sub(target_block_height) + 1
                >= args.confirmations,
            "Not enough blocks confirmed"
        );

        let header = self
            .headers_pool
            .get(&args.tx_block_blockhash)
            .unwrap_or_else(|| env::panic_str("cannot find requested transaction block"));

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

**File:** btc-types/src/contract_args.rs (L16-24)
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
```
