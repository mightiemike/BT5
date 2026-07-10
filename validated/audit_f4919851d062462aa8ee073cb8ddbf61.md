### Title
Deprecated `verify_transaction_inclusion` Remains Publicly Callable, Bypassing Coinbase Merkle Proof Integrity Check — (`contract/src/lib.rs`)

### Summary
`verify_transaction_inclusion` (v1) is still a live, unrestricted public NEAR contract method. The coinbase Merkle proof integrity check introduced in `verify_transaction_inclusion_v2` to mitigate the 64-byte transaction forgery attack is only enforced on the v2 path. Any unprivileged caller can invoke v1 directly and receive a `true` proof result without the coinbase validation, exactly mirroring the original report's pattern where a certain path bypasses a mandatory integrity check.

### Finding Description
`verify_transaction_inclusion_v2` was added to close the 64-byte transaction Merkle proof forgery vulnerability (https://www.bitmex.com/blog/64-Byte-Transactions). It enforces two checks before delegating to v1:

1. `merkle_proof.len() == coinbase_merkle_proof.len()`
2. The coinbase transaction's Merkle proof must reconstruct the block's `merkle_root` [1](#0-0) 

The v1 function performs neither check. It only verifies that the supplied `tx_id` and `merkle_proof` reconstruct the block's `merkle_root`: [2](#0-1) 

The `#[deprecated]` attribute is a Rust compiler hint only — it emits a warning at compile time but imposes no runtime restriction. The function remains `pub` and is fully callable as a NEAR contract method by any account: [3](#0-2) 

### Impact Explanation
An attacker can craft a 64-byte value that is simultaneously a valid SHA-256 hash of an internal Merkle tree node and a plausible `tx_id`. Passing this value to `verify_transaction_inclusion` (v1) with a matching `merkle_proof` will cause the function to return `true` for a transaction that does not exist in the block. Any downstream NEAR contract or application that calls v1 directly — or that was written before v2 existed — will accept a forged proof of transaction inclusion.

### Likelihood Explanation
The entry path requires no privilege: any NEAR account can call `verify_transaction_inclusion` with attacker-controlled `ProofArgs`. The 64-byte forgery technique is well-documented and has known tooling. Downstream contracts that integrated before v2 was introduced are the most likely victims, but the method remains callable by anyone today.

### Recommendation
Remove `verify_transaction_inclusion` (v1) as a public contract method, or add a `#[private]` attribute so it can only be called internally by `verify_transaction_inclusion_v2`. The `#[deprecated]` marker alone provides no on-chain enforcement.

### Proof of Concept
1. Identify a real mainchain block `B` whose `merkle_root` is known.
2. Construct a 64-byte value `fake_tx` that is the concatenation of two sibling hashes at depth 1 of the Merkle tree (an internal node hash). This value hashes to the `merkle_root` with a one-element proof.
3. Call `verify_transaction_inclusion` with `tx_id = fake_tx`, `tx_block_blockhash = B`, `tx_index = 0`, `merkle_proof = []` (or a crafted single-element proof), `confirmations = 1`.
4. The function computes `compute_root_from_merkle_proof(fake_tx, 0, &proof)` and compares it to `header.block_header.merkle_root`. With the crafted input this comparison succeeds and the function returns `true`, despite `fake_tx` not being a real transaction in block `B`.
5. The same call to `verify_transaction_inclusion_v2` would fail at the coinbase proof check (line 358–365), confirming the bypass is exclusive to the v1 path. [4](#0-3) [5](#0-4)

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
