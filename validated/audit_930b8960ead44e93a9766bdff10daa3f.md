### Title
Deprecated `verify_transaction_inclusion` bypasses per-function pause on `verify_transaction_inclusion_v2`, enabling 64-byte Merkle forgery during incident response - (File: `contract/src/lib.rs`)

---

### Summary

The `near_plugins` `#[pause]` macro assigns each decorated function its own independent pause feature keyed by function name. `verify_transaction_inclusion` (deprecated) and `verify_transaction_inclusion_v2` therefore have **separate, independent pause states**. When an admin pauses `verify_transaction_inclusion_v2` — the expected incident-response action — the deprecated `verify_transaction_inclusion` remains fully callable by any unprivileged NEAR account. That deprecated path explicitly lacks the coinbase-merkle-proof guard that blocks the 64-byte transaction forgery attack, so an attacker can obtain a fraudulent `true` verification result while the protocol is nominally in a protected state.

---

### Finding Description

`near_plugins` `#[pause]` uses the Rust function name as the feature identifier. The `Role` enum itself confirms per-function granularity: `UnrestrictedSubmitBlocks` bypasses only `submit_blocks`, and `UnrestrictedRunGC` bypasses only `run_mainchain_gc`. [1](#0-0) 

Both verification entry points carry `#[pause]`:

```
#[pause]
pub fn verify_transaction_inclusion(...)   // deprecated, line 287-288
#[pause]
pub fn verify_transaction_inclusion_v2(...)  // current, line 346-347
``` [2](#0-1) [3](#0-2) 

Because the feature names differ, `pa_pause_feature("verify_transaction_inclusion_v2")` leaves `"verify_transaction_inclusion"` active. Any NEAR caller can then invoke the deprecated function directly. The deprecated function's own doc-comment explicitly states it is vulnerable to the 64-byte transaction Merkle proof forgery attack and that it may return `true` for an internal Merkle-tree node rather than a real transaction hash. [4](#0-3) 

`verify_transaction_inclusion_v2` closes this hole by first verifying a coinbase Merkle proof before delegating to the deprecated function. [5](#0-4) 

When `verify_transaction_inclusion_v2` is paused, that coinbase guard is never reached; the attacker calls the deprecated endpoint directly and skips it entirely.

---

### Impact Explanation

A downstream dApp (bridge, DEX, custody system) that calls `verify_transaction_inclusion` — or that falls back to it when `verify_transaction_inclusion_v2` is unavailable — can be fed a crafted 64-byte input whose double-SHA256 hash collides with an internal Merkle node. The contract returns `true`, the dApp treats a non-existent Bitcoin transaction as confirmed, and funds are released or state is corrupted. The corrupted value is the boolean proof result returned to the consuming contract, which is the canonical output of the light-client API.

---

### Likelihood Explanation

The most realistic trigger is an incident response scenario: a bug is found in `verify_transaction_inclusion_v2`, the `PauseManager` pauses that specific feature, and the deprecated function is overlooked because it already carries its own `#[pause]` annotation (giving a false sense of independent safety). The attacker needs only to call the public NEAR method `verify_transaction_inclusion` with a crafted proof — no privileged role, no key leak, no social engineering.

---

### Recommendation

1. **Couple the pause states**: override the feature name so both functions share one pausable feature, e.g. `#[pause(name = "verify_transaction_inclusion")]` on both, or use a single dispatcher.
2. **Remove the deprecated endpoint**: since `verify_transaction_inclusion_v2` already delegates to `verify_transaction_inclusion` internally, the deprecated public surface can be removed entirely, eliminating the bypass path.
3. **Alternatively**, add an explicit check inside `verify_transaction_inclusion` that panics if `verify_transaction_inclusion_v2` is paused, ensuring the two states are always consistent.

---

### Proof of Concept

```
# 1. Admin pauses only the v2 endpoint (e.g. via near-cli):
near call <contract> pa_pause_feature '{"feature_name":"verify_transaction_inclusion_v2"}' \
  --accountId pause_manager.near

# 2. Attacker constructs a ProofArgs where tx_id is a 64-byte-aligned
#    internal Merkle node hash that matches the stored merkle_root
#    (64-byte forgery as documented at https://www.bitmex.com/blog/64-Byte-Transactions).

# 3. Attacker calls the deprecated, still-active endpoint:
near call <contract> verify_transaction_inclusion \
  '{"args": <borsh-encoded ProofArgs with forged tx_id>}' \
  --accountId attacker.near

# 4. Contract returns `true` — the coinbase guard in v2 was never executed.
# 5. Any dApp consuming this result treats the forged transaction as confirmed
#    and releases funds.
```

The broken invariant: while `verify_transaction_inclusion_v2` is paused, the contract still exposes a reachable public path that returns a verification result without the coinbase-proof guard, violating the protocol's stated security property against 64-byte Merkle forgery. [6](#0-5)

### Citations

**File:** contract/src/lib.rs (L40-46)
```rust
pub enum Role {
    /// May pause and unpause features.
    PauseManager,
    /// Allows to use contract API even after contract is paused
    UnrestrictedSubmitBlocks,
    // Allows to use `run_mainchain_gc` API on a paused contract
    UnrestrictedRunGC,
```

**File:** contract/src/lib.rs (L265-279)
```rust
    /// # Deprecated
    /// Use [`verify_transaction_inclusion_v2`] instead, which includes coinbase merkle proof validation
    /// to mitigate the 64-byte transaction Merkle proof forgery vulnerability:
    /// https://www.bitmex.com/blog/64-Byte-Transactions
    ///
    /// @param `tx_id` transaction identifier
    /// @param `tx_block_blockhash` block hash at which transacton is supposedly included
    /// @param `tx_index` index of transaction in the block's tx merkle tree
    /// @param `merkle_proof` merkle tree path (concatenated LE sha256 hashes) (does not contain initial `transaction_hash` and `merkle_root`)
    /// @param confirmations how many confirmed blocks we want to have before the transaction is valid
    /// @return True if `tx_id` is at the claimed position in the block at the given blockhash, False otherwise
    ///
    /// # Warning
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

**File:** contract/src/lib.rs (L346-347)
```rust
    #[pause]
    pub fn verify_transaction_inclusion_v2(&self, #[serializer(borsh)] args: ProofArgsV2) -> bool {
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
