### Title
Deprecated `verify_transaction_inclusion` Remains Publicly Callable, Bypassing the Coinbase Proof Validation Added in v2 to Fix the 64-Byte Merkle Forgery — (`contract/src/lib.rs`)

### Summary

`verify_transaction_inclusion` (v1) is still a live, publicly callable NEAR contract method despite being deprecated. It shares the same core proof data layout as `verify_transaction_inclusion_v2` but lacks the coinbase Merkle proof check that v2 introduced specifically to block the 64-byte transaction Merkle forgery attack. Any unprivileged NEAR caller can bypass v2's security fix by calling v1 directly, obtaining a `true` proof-verification result for a non-existent transaction.

### Finding Description

`verify_transaction_inclusion_v2` was introduced to fix a known Bitcoin Merkle tree second-preimage vulnerability (the "64-byte transaction" attack). It enforces an additional coinbase proof check before delegating to v1:

```
verify_transaction_inclusion_v2(ProofArgsV2):
  1. require merkle_proof.len() == coinbase_merkle_proof.len()
  2. require compute_root_from_merkle_proof(coinbase_tx_id, 0, coinbase_merkle_proof) == block.merkle_root
  3. call verify_transaction_inclusion(args.into())   ← drops coinbase fields
``` [1](#0-0) 

`ProofArgs` (v1) is a strict subset of `ProofArgsV2` (v2) — the two structs share identical fields for `tx_id`, `tx_block_blockhash`, `tx_index`, `merkle_proof`, and `confirmations`; v2 merely appends `coinbase_tx_id` and `coinbase_merkle_proof`: [2](#0-1) 

The `From<ProofArgsV2> for ProofArgs` conversion explicitly drops the coinbase fields, confirming the shared layout: [3](#0-2) 

`verify_transaction_inclusion` is marked `#[deprecated]` in Rust source, but Rust's `#[deprecated]` attribute only emits a compiler warning — it does **not** remove the function from the compiled WASM binary or prevent external callers from invoking it via NEAR RPC. The function carries `#[pause]` (not `#[private]`), so when the contract is not paused it is callable by any NEAR account: [4](#0-3) 

The v1 function performs no coinbase proof validation. It only recomputes the Merkle root from the supplied `tx_id` and `merkle_proof` and compares it to the stored block header's `merkle_root`: [5](#0-4) 

The contract's own documentation acknowledges the consequence: "This function may return `true` if the provided `tx_id` is a hash of an internal node in the Merkle tree rather than a valid transaction hash." [6](#0-5) 

### Impact Explanation

An attacker can call `verify_transaction_inclusion` directly with a crafted `tx_id` that is actually a 64-byte internal Merkle tree node (two concatenated 32-byte child hashes). Because the function only checks that `compute_root_from_merkle_proof(tx_id, tx_index, merkle_proof) == block.merkle_root`, and because an internal node hash satisfies this equation by construction, the function returns `true` for a transaction that does not exist on Bitcoin. Any downstream NEAR contract or application that consumes this boolean to authorize an action — e.g., releasing bridged funds, confirming a Bitcoin deposit, or unlocking collateral — is deceived into treating a forged proof as valid. This is a critical proof-verification integrity failure.

### Likelihood Explanation

Likelihood is high. The entry path requires no privileged access: any NEAR account can call `verify_transaction_inclusion` via a standard function call. The 64-byte Merkle forgery attack is well-documented (CVE-2012-2459 class), the inputs are computable from public Bitcoin blockchain data, and the contract already stores the block headers needed to identify a suitable internal node. The only prerequisite is that the target block is present in `headers_pool` and on the current main chain, which is the normal operating state of the contract.

### Recommendation

Remove `verify_transaction_inclusion` from the public WASM ABI entirely, or gate it with `#[private]` so it is only callable by the contract itself (as it is already used internally by `verify_transaction_inclusion_v2`). Callers that still use v1 must be migrated to v2. If backward compatibility is required during a transition period, add the same coinbase proof validation to v1 so both functions enforce equivalent security invariants.

### Proof of Concept

1. Identify any Bitcoin block stored in the contract's `headers_pool` with at least two transactions (so the Merkle tree has internal nodes).
2. Compute the hash of an internal Merkle tree node at depth ≥ 1 (e.g., `H = SHA256d(tx0_hash || tx1_hash)`). This `H` is a valid internal node whose Merkle path to the root is known.
3. Construct a `ProofArgs` where `tx_id = H`, `tx_index` and `merkle_proof` are set to the path from `H` to the Merkle root.
4. Call `verify_transaction_inclusion` on the deployed contract with these arguments.
5. The function returns `true` because `compute_root_from_merkle_proof(H, index, proof) == block.merkle_root` holds by construction, even though `H` is not a real transaction hash.
6. A downstream contract that calls `verify_transaction_inclusion` and acts on the `true` result (e.g., releasing funds) is now exploited. [7](#0-6) [8](#0-7)

### Citations

**File:** contract/src/lib.rs (L278-279)
```rust
    /// This function may return `true` if the provided `tx_id` is a hash of an internal node in the Merkle tree rather than a valid transaction hash.
    /// We assume that validation of whether the `tx_id` corresponds to a valid transaction hash is performed at a higher level of verification.
```

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

**File:** btc-types/src/contract_args.rs (L16-47)
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
