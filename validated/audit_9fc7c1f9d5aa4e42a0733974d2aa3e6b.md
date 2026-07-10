### Title
Deprecated `verify_transaction_inclusion` Remains Publicly Callable, Enabling 64-Byte Merkle Proof Forgery — (File: `contract/src/lib.rs`)

---

### Summary

The contract simultaneously exposes two transaction-inclusion verification functions with materially different security properties. The deprecated `verify_transaction_inclusion` (v1) lacks coinbase Merkle proof validation and is still reachable by any unprivileged NEAR caller. The current `verify_transaction_inclusion_v2` (v2) adds that validation specifically to block the 64-byte Merkle proof forgery attack. Because both functions are live at the same time, an attacker can route a forged proof through v1 and receive a `true` result that v2 would reject — an exact cross-version desynchronization analog to the oracle-arbitrage class described in the external report.

---

### Finding Description

The contract defines two public verification entry points:

```rust
// v1 — deprecated, no coinbase proof check
#[deprecated(since = "0.5.0", note = "Use `verify_transaction_inclusion_v2` instead.")]
#[pause]
pub fn verify_transaction_inclusion(&self, #[serializer(borsh)] args: ProofArgs) -> bool { … }

// v2 — current, adds coinbase proof validation
#[pause]
pub fn verify_transaction_inclusion_v2(&self, #[serializer(borsh)] args: ProofArgsV2) -> bool { … }
``` [1](#0-0) 

The `#[deprecated]` Rust attribute emits compiler warnings for crate-internal callers only. It does **not** remove the function from the compiled WASM binary and does **not** prevent any external NEAR account from calling it on-chain. The function is `pub` and gated only by `#[pause]`, which is inactive by default.

v2 enforces an additional invariant before delegating to v1:

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
``` [2](#0-1) 

When a caller invokes v1 directly, that coinbase check is entirely skipped. The only check performed is:

```rust
merkle_tools::compute_root_from_merkle_proof(
    args.tx_id,
    usize::try_from(args.tx_index).unwrap(),
    &args.merkle_proof,
) == header.block_header.merkle_root
``` [3](#0-2) 

The 64-byte forgery technique (documented at https://www.bitmex.com/blog/64-Byte-Transactions, referenced in the contract's own deprecation notice) allows an attacker to construct a value that is simultaneously a valid internal Merkle tree node and a plausible "transaction hash." Without the coinbase anchor, `compute_root_from_merkle_proof` cannot distinguish a real leaf from a forged internal node, so the comparison can be made to succeed for a transaction that was never included in the block.

The `compute_root_from_merkle_proof` function in `merkle-tools` performs no length or structure validation beyond iterating the supplied proof hashes:

```rust
pub fn compute_root_from_merkle_proof(
    transaction_hash: H256,
    transaction_position: usize,
    merkle_proof: &Vec<H256>,
) -> H256 {
    let mut current_hash = transaction_hash;
    let mut current_position = transaction_position;
    for proof_hash in merkle_proof {
        …
    }
    current_hash
}
``` [4](#0-3) 

---

### Impact Explanation

Any NEAR contract that calls `verify_transaction_inclusion` (v1) — whether because it was integrated before v2 existed, or because a developer chose the wrong entry point — can be fed a forged proof that returns `true` for a Bitcoin transaction that was never confirmed. Downstream actions gated on that `true` result (fund releases, bridge unlocks, cross-chain settlements) would execute on a fraudulent basis. The two simultaneously live functions produce divergent results for the same adversarial input, directly mirroring the oracle-desynchronization impact described in the external report.

---

### Likelihood Explanation

- No privileged role is required; any NEAR account can call `verify_transaction_inclusion`.
- The 64-byte Merkle forgery technique is publicly documented and the contract's own deprecation notice links to it, making the attack vector well-known.
- The function is not paused by default and nothing in the contract prevents it from being called indefinitely.
- Recipient contracts integrated before v2 was introduced are already calling v1 in production.

---

### Recommendation

Remove `verify_transaction_inclusion` from the contract entirely. If a grace period is required, replace the function body with an unconditional panic:

```rust
pub fn verify_transaction_inclusion(&self, …) -> bool {
    env::panic_str("verify_transaction_inclusion is removed; use verify_transaction_inclusion_v2");
}
```

Do not rely on `#[deprecated]` to prevent on-chain invocation. The attribute has no effect on WASM-exported functions called by external accounts.

---

### Proof of Concept

1. Attacker identifies a Bitcoin block already stored in the contract's `headers_pool` with a known `merkle_root`.
2. Using the 64-byte Merkle forgery technique, attacker constructs a `forged_tx_id` (a 32-byte value that is actually an internal Merkle node) and a `merkle_proof` path such that `compute_root_from_merkle_proof(forged_tx_id, index, proof) == merkle_root`.
3. Attacker calls `verify_transaction_inclusion` directly (not v2) with `tx_id = forged_tx_id`, `tx_block_blockhash = <known block>`, `tx_index = <crafted index>`, `merkle_proof = <crafted path>`, `confirmations = 1`.
4. v1 executes the single Merkle root comparison with no coinbase anchor check and returns `true`.
5. v2 called with the same inputs would fail at the `require!` on the coinbase proof and return `false` / panic.
6. A recipient contract relying on the `true` result from v1 releases funds or executes a cross-chain action for a Bitcoin transaction that does not exist.

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

**File:** contract/src/lib.rs (L358-368)
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
