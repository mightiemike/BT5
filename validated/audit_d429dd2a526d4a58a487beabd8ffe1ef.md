### Title
Deprecated `verify_transaction_inclusion` Remains Publicly Callable, Bypassing 64-Byte Merkle Proof Forgery Protection — (`contract/src/lib.rs`)

### Summary

`verify_transaction_inclusion` (v1) is still a live, unrestricted public entry point on the contract. Any unprivileged NEAR caller can invoke it directly, bypassing the coinbase Merkle proof validation that `verify_transaction_inclusion_v2` was specifically introduced to enforce. A recipient contract that calls v1 directly — or an attacker who supplies a crafted proof to a downstream consumer — can obtain a `true` result for a forged transaction inclusion proof.

### Finding Description

`verify_transaction_inclusion_v2` was introduced to mitigate the 64-byte transaction Merkle proof forgery vulnerability (documented at https://www.bitmex.com/blog/64-Byte-Transactions). It does so by first validating a coinbase Merkle proof against the block's Merkle root before delegating to v1: [1](#0-0) 

The protection is entirely in v2's preamble. v1 itself performs no such check — it only recomputes the Merkle root from the supplied `tx_id` and proof path and compares it to the stored header's `merkle_root`: [2](#0-1) 

Despite being marked `#[deprecated]`, v1 carries no access restriction. The `#[deprecated]` attribute in Rust is a compile-time lint only; it does not prevent on-chain invocation. The function is `pub`, decorated only with `#[pause]` (which gates on the pause flag, not on caller identity), and is fully reachable by any NEAR account: [3](#0-2) 

The contract's own documentation acknowledges the vulnerability explicitly: [4](#0-3) 

### Impact Explanation

An attacker who knows the Merkle tree structure of a real Bitcoin block can craft a `tx_id` that is an internal 64-byte Merkle node hash. By supplying a proof path that correctly computes to the block's `merkle_root`, `verify_transaction_inclusion` returns `true` for a hash that does not correspond to any real transaction. Any downstream NEAR contract that calls v1 directly — or that trusts a result obtained via v1 — will accept a forged proof of transaction inclusion. This breaks the core SPV guarantee the contract is designed to provide.

### Likelihood Explanation

The entry point is unrestricted and callable by any NEAR account as a view call (no deposit, no role). The 64-byte forgery technique is well-documented and has known tooling. Any integrator who reads the contract ABI sees two `verify_transaction_inclusion` methods and may use v1 without understanding the security difference. The relayer's own `NearClient` still exposes a `verify_transaction_inclusion` (v1) wrapper: [5](#0-4) 

### Recommendation

Remove `verify_transaction_inclusion` (v1) from the public ABI entirely, or add a caller restriction (e.g., `#[private]`) so it can only be called internally by `verify_transaction_inclusion_v2`. The `#[deprecated]` attribute provides no on-chain enforcement. Keeping a publicly callable, unprotected v1 alongside a protected v2 creates a permanent bypass path that any caller can exploit.

### Proof of Concept

1. Identify a real Bitcoin block already stored in the contract's `headers_pool`.
2. Inspect its Merkle tree. Select an internal node `N` at depth `d` whose 64-byte concatenated child hashes are a valid input to the SHA-256d second-preimage attack.
3. Construct a `ProofArgs` where `tx_id = N` and `merkle_proof` is the sibling path from depth `d` up to the root.
4. Call `verify_transaction_inclusion` directly (not v2) from any unprivileged NEAR account.
5. The function computes `compute_root_from_merkle_proof(N, index, proof)` == `header.merkle_root` and returns `true`.
6. No coinbase proof is ever checked. The result is indistinguishable from a legitimate inclusion proof to any consumer. [6](#0-5) [7](#0-6)

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

**File:** contract/CLAUDE.md (L64-66)
```markdown
`verify_transaction_inclusion(ProofArgs)` — SPV proof: given a tx hash, block hash, and merkle proof, verifies the transaction is in the block by recomputing the merkle root.

**Important**: This function is vulnerable to the standard Bitcoin merkle tree second-preimage attack — it may return `true` for an internal node hash rather than a real transaction hash. Block headers do not contain the transaction count, so proof depth cannot be validated on-chain. Callers MUST validate that the `tx_id` corresponds to a valid transaction (e.g., by verifying raw transaction data) before trusting the inclusion proof.
```

**File:** relayer/src/near_client.rs (L412-437)
```rust
    #[allow(dead_code)]
    pub async fn verify_transaction_inclusion(
        &self,
        transaction_hash: H256,
        transaction_position: usize,
        transaction_block_blockhash: H256,
        merkle_proof: Vec<H256>,
        confirmations: u64,
    ) -> Result<bool, Box<dyn std::error::Error + Send + Sync>> {
        let args = btc_types::contract_args::ProofArgs {
            tx_id: transaction_hash,
            tx_block_blockhash: transaction_block_blockhash,
            tx_index: transaction_position.try_into()?,
            merkle_proof,
            confirmations,
        };

        let tx_hash = self
            .submit_tx(
                self.sign_tx(VERIFY_TRANSACTION_INCLUSION, to_vec(&args)?, 0, None)
                    .await?,
            )
            .await?;
        let response = self.get_tx_status(tx_hash).await?;
        Self::parse_verify_response(response, VERIFY_TRANSACTION_INCLUSION)
    }
```
