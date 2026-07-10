### Title
Deprecated `verify_transaction_inclusion` Remains Publicly Callable, Enabling 64-Byte Merkle Proof Forgery — (`contract/src/lib.rs`)

### Summary

`verify_transaction_inclusion` is marked `#[deprecated]` but remains a live, unrestricted public entry point on the contract. Any unprivileged NEAR caller can invoke it directly, bypassing the coinbase-proof guard introduced in `verify_transaction_inclusion_v2`. This allows an attacker to forge a proof that an arbitrary fake "transaction" was included in a real Bitcoin block, causing the function to return `true` for a transaction that never existed.

### Finding Description

The contract exposes two proof-verification entry points:

- `verify_transaction_inclusion` — the original function, deprecated since v0.5.0, which computes only a single Merkle path from `tx_id` to the block's `merkle_root`. [1](#0-0) 

- `verify_transaction_inclusion_v2` — the replacement, which first validates a coinbase Merkle proof before delegating to the v1 logic. [2](#0-1) 

The v1 function carries an explicit code-level warning:

> "This function may return `true` if the provided `tx_id` is a hash of an internal node in the Merkle tree rather than a valid transaction hash." [3](#0-2) 

Despite this, `verify_transaction_inclusion` is still decorated with `#[pause]` (not `#[private]` or removed), meaning it is fully reachable by any NEAR account. [4](#0-3) 

The 64-byte transaction attack (documented at https://www.bitmex.com/blog/64-Byte-Transactions) works as follows:

1. Bitcoin's Merkle tree hashes pairs of 32-byte child hashes: `SHA256d(left || right)` — producing a 64-byte input.
2. An attacker crafts a 64-byte blob that, when treated as a "transaction", has the same double-SHA256 hash as a real internal Merkle tree node in a known block.
3. The attacker supplies this blob as `tx_id` along with a Merkle path that leads from that internal node up to the block's `merkle_root`.
4. `compute_root_from_merkle_proof(tx_id, tx_index, &merkle_proof)` produces the correct `merkle_root`, so the function returns `true`. [5](#0-4) 

The v2 function defeats this by requiring the caller to also prove the coinbase transaction (index 0) against the same `merkle_root`. A 64-byte internal node cannot simultaneously satisfy both the coinbase proof and the target-tx proof. [6](#0-5) 

**Analog to the external report:** The report flags `setLastPerformTimestamp()` being left `external` for testing convenience when it should be `internal` — a function whose public visibility creates a concrete exploit path. Here, `verify_transaction_inclusion` is left as a public contract method despite being deprecated and carrying a known forgery vulnerability, for the same class of reason (backward compatibility / convenience). The fix in both cases is identical in structure: restrict or remove the dangerous public entry point.

### Impact Explanation

Any external NEAR DApp that calls `verify_transaction_inclusion` (rather than `verify_transaction_inclusion_v2`) will receive a forged `true` result for a Bitcoin transaction that was never broadcast or mined. This corrupts the proof-verification guarantee that is the contract's core security property. Downstream contracts that gate asset releases, bridge withdrawals, or other value-bearing actions on this result can be drained or manipulated.

### Likelihood Explanation

The function is publicly documented in the ABI and callable by any NEAR account with no deposit or role requirement. The 64-byte Merkle forgery technique is well-known and has published tooling. The only friction is finding a suitable internal node in a real block — a one-time offline computation. Likelihood is **medium-high** given the financial incentive of any bridge or DApp built on top of this contract.

### Recommendation

Remove `verify_transaction_inclusion` from the public ABI entirely, or gate it with `#[private]` so it can only be called by the contract itself (as `verify_transaction_inclusion_v2` already does internally). [7](#0-6) 

If backward compatibility must be preserved for a transition period, add an explicit `require!(false, "use verify_transaction_inclusion_v2")` guard at the top of the function body so it always panics when called externally.

### Proof of Concept

```
# Attacker selects a real mainchain block B with known merkle_root R.
# Offline: find an internal Merkle node N (64 bytes) such that
#   SHA256d(N) == some_hash H, and a sibling path P where
#   compute_root_from_merkle_proof(H, idx, P) == R.
#
# Call on NEAR (no special role needed):
near call <contract> verify_transaction_inclusion \
  --args-borsh <borsh({
      tx_id: H,
      tx_block_blockhash: B,
      tx_index: idx,
      merkle_proof: P,
      confirmations: 1
  })> \
  --accountId attacker.near
#
# Returns: true
# Actual Bitcoin transaction H: does not exist.
```

The call succeeds because `verify_transaction_inclusion` applies no coinbase anchor check, and `compute_root_from_merkle_proof` is a pure hash computation that cannot distinguish a real transaction hash from an internal node hash. [5](#0-4)

### Citations

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
