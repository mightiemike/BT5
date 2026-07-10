### Title
Deprecated `verify_transaction_inclusion` Remains Publicly Callable, Bypassing 64-Byte Merkle Proof Forgery Protection — (`contract/src/lib.rs`)

---

### Summary

`verify_transaction_inclusion` was deprecated in favour of `verify_transaction_inclusion_v2`, which adds a mandatory coinbase Merkle proof check to mitigate the 64-byte transaction forgery vulnerability. Despite the deprecation, the old function is still a live, unrestricted public NEAR contract method. Any unprivileged caller can invoke it directly, bypassing the coinbase proof guard entirely and obtaining a `true` verification result for a fabricated transaction.

---

### Finding Description

`verify_transaction_inclusion_v2` was introduced specifically to close the 64-byte transaction Merkle proof forgery attack (documented inline and in the function's doc comment). It enforces that the coinbase transaction at index 0 hashes to the block's Merkle root before delegating to the old function: [1](#0-0) 

The old function, however, is still `pub` and carries only a Rust-level `#[deprecated]` attribute — which is a compiler lint, not a runtime guard. It remains a fully reachable NEAR contract method: [2](#0-1) 

`ProofArgs` (the input type for the old function) contains no `coinbase_tx_id` or `coinbase_merkle_proof` fields: [3](#0-2) 

`ProofArgsV2` adds those fields: [4](#0-3) 

Because `verify_transaction_inclusion` accepts `ProofArgs` directly and performs only a raw Merkle root comparison with no coinbase anchor, a caller who supplies an internal 64-byte Merkle node as `tx_id` will receive `true`.

---

### Impact Explanation

Any downstream NEAR contract that calls `verify_transaction_inclusion` (rather than v2) to gate a cross-chain action — e.g., releasing bridged assets, minting tokens, or settling a payment — can be deceived into accepting a fabricated Bitcoin transaction as confirmed. The corrupted value is the boolean proof result returned to the caller: it becomes `true` for a transaction that was never broadcast or mined.

---

### Likelihood Explanation

The entry path requires no privilege: any NEAR account can call `verify_transaction_inclusion` directly. The 64-byte forgery technique is publicly documented (the contract itself links to the BitMEX writeup). A motivated attacker who controls a Bitcoin miner (or can find a block whose internal Merkle node collides with a desired 64-byte pattern) can construct a valid-looking `ProofArgs` payload. Downstream integrators who read the ABI or SDK and pick the simpler, argument-lighter function are a realistic population of victims.

---

### Recommendation

Remove the `pub` visibility from `verify_transaction_inclusion` or gate it with an explicit `#[private]` attribute so it is only reachable as an internal helper called from `verify_transaction_inclusion_v2`. A Rust `#[deprecated]` attribute alone does not prevent external NEAR callers from invoking the method. Alternatively, delete the function body and have `verify_transaction_inclusion_v2` inline the Merkle root comparison directly, eliminating the callable surface entirely.

---

### Proof of Concept

1. Attacker identifies a Bitcoin block `B` already stored in the contract's `headers_pool`.
2. Attacker computes an internal Merkle node `N` (32 bytes left child ‖ 32 bytes right child = 64 bytes) whose double-SHA256 hash equals some value `fake_txid`.
3. Attacker constructs `ProofArgs { tx_id: fake_txid, tx_block_blockhash: B, tx_index: <index of N>, merkle_proof: <siblings>, confirmations: 1 }`.
4. Attacker calls `verify_transaction_inclusion(args)` directly on the NEAR contract.
5. The function computes `compute_root_from_merkle_proof(fake_txid, index, siblings)` — this equals the block's real Merkle root because `N` is a genuine internal node.
6. The function returns `true`.
7. Any downstream contract that trusted this result now believes `fake_txid` is a confirmed Bitcoin transaction. [5](#0-4)

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
