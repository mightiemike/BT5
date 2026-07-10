### Title
Deprecated `verify_transaction_inclusion` Remains Publicly Callable, Allowing Unprivileged Callers to Bypass Coinbase-Proof Guard and Forge Transaction Inclusion Results - (File: contract/src/lib.rs)

### Summary
`verify_transaction_inclusion` is marked deprecated and documented as vulnerable to the 64-byte Merkle second-preimage attack, but it remains a live, unguarded public contract method. Any unprivileged NEAR caller can invoke it directly, bypassing the coinbase-proof protection that `verify_transaction_inclusion_v2` enforces, and obtain a forged `true` inclusion result for a transaction that was never included in a Bitcoin block.

### Finding Description
The contract exposes two transaction-inclusion verification methods:

- `verify_transaction_inclusion_v2` (lines 347–368): the secure path. It first validates a coinbase Merkle proof against the stored block's `merkle_root`, then delegates to the deprecated function. This coinbase check is the only on-chain defence against the 64-byte internal-node forgery attack.
- `verify_transaction_inclusion` (lines 288–323): the deprecated path. It carries only a `#[pause]` attribute — no role guard, no coinbase check. It is callable by any NEAR account at any time the contract is not paused. [1](#0-0) 

The `#[deprecated]` Rust attribute is a compiler lint; it does not remove the method from the compiled WASM or restrict who may call it on-chain. The contract's own documentation acknowledges the vulnerability: [2](#0-1) 

The 64-byte attack works because Bitcoin Merkle trees do not encode the transaction count in the block header. An attacker who knows an internal 64-byte node value of a real block's Merkle tree can supply that value as `tx_id` with a crafted `merkle_proof` and `tx_index`, causing `compute_root_from_merkle_proof` to reconstruct the correct `merkle_root` and return `true`. [3](#0-2) 

`verify_transaction_inclusion_v2` closes this gap by requiring the caller to also prove the coinbase transaction is at index 0 of the same block, which is computationally infeasible to forge simultaneously. But because the deprecated function is still reachable directly, the v2 guard is trivially bypassed. [4](#0-3) 

### Impact Explanation
Any downstream NEAR contract that calls `verify_transaction_inclusion` to gate asset releases, cross-chain bridge actions, or other value-bearing operations will receive a forged `true` result. The attacker does not need any role, stake, or privileged key — only knowledge of an internal Merkle node of any block already stored in the contract's `headers_pool`. The corrupted state is the proof result itself: a `true` return value for a transaction that does not exist.

**Impact: 5 / 10**

### Likelihood Explanation
The attack requires: (1) a block already accepted into the contract's mainchain, (2) knowledge of any 64-byte internal Merkle node of that block (derivable from public Bitcoin block data), and (3) a downstream consumer that calls the deprecated function. The function is publicly documented and the 64-byte attack is well-known in the Bitcoin security community.

**Likelihood: 3 / 10**

### Recommendation
Remove `verify_transaction_inclusion` from the compiled contract entirely, or add an explicit `env::panic_str("use verify_transaction_inclusion_v2")` as the first statement in the function body so that any direct on-chain call unconditionally fails. A Rust `#[deprecated]` attribute alone is insufficient — it only produces a compiler warning for Rust callers and has no effect on NEAR RPC callers invoking the method by name.

### Proof of Concept
1. Identify any block hash `B` stored in the contract's `mainchain_header_to_height` map.
2. Obtain the full block from Bitcoin RPC and compute any internal 64-byte Merkle node `N` at depth `d` with sibling path `P`.
3. Call `verify_transaction_inclusion` directly (not v2) with:
   - `tx_id = N`
   - `tx_block_blockhash = B`
   - `tx_index` = the leaf index that `N` represents at depth `d`
   - `merkle_proof = P`
   - `confirmations = 0`
4. The function recomputes the Merkle root from `N` and `P`, matches the stored `merkle_root`, and returns `true` — despite `N` not being a real transaction hash.
5. No role, stake, or privileged access is required; the call succeeds for any unprivileged NEAR account. [5](#0-4)

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

**File:** contract/src/lib.rs (L346-368)
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
```

**File:** contract/CLAUDE.md (L66-66)
```markdown
**Important**: This function is vulnerable to the standard Bitcoin merkle tree second-preimage attack — it may return `true` for an internal node hash rather than a real transaction hash. Block headers do not contain the transaction count, so proof depth cannot be validated on-chain. Callers MUST validate that the `tx_id` corresponds to a valid transaction (e.g., by verifying raw transaction data) before trusting the inclusion proof.
```
