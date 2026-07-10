### Title
Deprecated `verify_transaction_inclusion` Remains Publicly Callable, Enabling 64-Byte Merkle Proof Forgery - (File: contract/src/lib.rs)

### Summary
The contract exposes two SPV proof verification endpoints: the deprecated `verify_transaction_inclusion` (v1) and the current `verify_transaction_inclusion_v2`. The v1 function is marked `#[deprecated]` but remains fully callable at runtime by any unprivileged NEAR account. It lacks the coinbase Merkle proof validation that v2 introduces specifically to block the 64-byte transaction Merkle proof forgery attack. Any caller that invokes v1 directly — including downstream dApps or adversarial proof submitters — can receive a `true` result for a transaction that was never included in any block.

### Finding Description
`verify_transaction_inclusion` is annotated `#[deprecated(since = "0.5.0", note = "Use verify_transaction_inclusion_v2 instead.")]` and carries an explicit code warning that it "may return `true` if the provided `tx_id` is a hash of an internal node in the Merkle tree rather than a valid transaction hash." Despite this, the function is decorated only with `#[pause]` — it is not removed, not access-controlled, and not gated behind any role check. It remains a live, publicly callable NEAR method.

The v2 function closes the forgery window by first verifying a coinbase Merkle proof before delegating to v1:

```rust
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

v1 performs no such check. `compute_root_from_merkle_proof` in `merkle-tools/src/lib.rs` is a pure path-traversal function: it accepts any `transaction_hash` and any `merkle_proof` and computes a root. If the supplied `tx_id` is a 64-byte internal node (two concatenated 32-byte hashes), the computed root can match the stored `merkle_root` without any real transaction being present. [1](#0-0) [2](#0-1) [3](#0-2) 

### Impact Explanation
A successful call to `verify_transaction_inclusion` with a crafted internal-node `tx_id` returns `true` to the caller. Any dApp or bridge contract that consumes this result treats the forged transaction as confirmed on-chain. This corrupts the canonical SPV proof result — the single security guarantee the light client exports — and can be used to unlock funds, trigger cross-chain actions, or satisfy settlement conditions that depend on Bitcoin transaction finality. The broken invariant is: `verify_transaction_inclusion` returning `true` must mean the supplied `tx_id` is a real transaction committed in the block's Merkle tree; the 64-byte attack breaks this invariant without touching any privileged state. [4](#0-3) 

### Likelihood Explanation
The attack requires no privileged role, no leaked key, and no social engineering. Any NEAR account can call `verify_transaction_inclusion` directly. The 64-byte Merkle forgery technique is publicly documented (referenced in the contract's own comments). The only friction is that a caller must deliberately choose the deprecated v1 endpoint over v2, which is realistic for: (a) dApps integrated before v2 was introduced that have not updated their call sites, (b) adversarial callers who inspect the ABI and select the weaker path intentionally. [5](#0-4) 

### Recommendation
Remove `verify_transaction_inclusion` from the public ABI entirely, or replace its body with an unconditional `env::panic_str("use verify_transaction_inclusion_v2")`. A Rust `#[deprecated]` attribute is a compile-time hint for Rust callers only; it provides zero runtime protection against NEAR RPC callers who invoke the method by name. Keeping the function live means the weaker verification path is permanently available to any caller. [6](#0-5) 

### Proof of Concept
1. Identify a block `B` already accepted by the contract (any hash in `mainchain_header_to_height`).
2. Retrieve `B`'s `merkle_root` from `headers_pool`.
3. Construct a fake `tx_id` that is the SHA256d of two concatenated 32-byte values such that the resulting Merkle path produces `merkle_root` (standard 64-byte CVE technique; tooling is publicly available).
4. Call `verify_transaction_inclusion` with `tx_block_blockhash = B`, `tx_id = <forged>`, `tx_index = <matching index>`, `merkle_proof = <crafted path>`, `confirmations = 1`.
5. The function returns `true`. No PoW mining, no privileged role, no contract state modification required.

`verify_transaction_inclusion_v2` would reject step 4 because the coinbase proof check would fail for the forged `tx_id`. [7](#0-6) [3](#0-2)

### Citations

**File:** contract/src/lib.rs (L166-198)
```rust
    #[payable]
    #[pause]
    #[trusted_relayer]
    pub fn submit_blocks(
        &mut self,
        #[serializer(borsh)] headers: Vec<BlockHeader>,
    ) -> PromiseOrValue<()> {
        let amount = env::attached_deposit();
        let initial_storage = env::storage_usage();
        let num_of_headers = headers.len().try_into().unwrap();

        for header in headers {
            self.submit_block_header(header, self.skip_pow_verification);
        }

        self.run_mainchain_gc(num_of_headers);
        let diff_storage_usage = env::storage_usage().saturating_sub(initial_storage);
        let required_deposit = env::storage_byte_cost().saturating_mul(diff_storage_usage.into());

        require!(
            amount >= required_deposit,
            format!("Required deposit {}", required_deposit)
        );

        let refund = amount.saturating_sub(required_deposit);
        if refund > NearToken::from_near(0) {
            Promise::new(env::predecessor_account_id())
                .transfer(refund)
                .into()
        } else {
            PromiseOrValue::Value(())
        }
    }
```

**File:** contract/src/lib.rs (L276-280)
```rust
    ///
    /// # Warning
    /// This function may return `true` if the provided `tx_id` is a hash of an internal node in the Merkle tree rather than a valid transaction hash.
    /// We assume that validation of whether the `tx_id` corresponds to a valid transaction hash is performed at a higher level of verification.
    ///
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
