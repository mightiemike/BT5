### Title
Deprecated `verify_transaction_inclusion` Remains Publicly Callable, Bypassing the 64-Byte Merkle Proof Forgery Protection Added in v2 - (File: `contract/src/lib.rs`)

---

### Summary

`verify_transaction_inclusion` (v1) is marked `#[deprecated]` but remains a fully public, on-chain-callable NEAR method. Any unprivileged caller can invoke it directly, bypassing the coinbase Merkle proof validation that `verify_transaction_inclusion_v2` was introduced to enforce. This is a direct structural analog to the TITN bridge bypass: a security check is applied to one entry point (`v2`) but not to an equivalent, still-reachable entry point (`v1`).

---

### Finding Description

The contract introduced `verify_transaction_inclusion_v2` specifically to mitigate the 64-byte transaction Merkle proof forgery vulnerability (CVE-2017-12842). The v2 function validates a coinbase Merkle proof before delegating to v1:

```rust
// contract/src/lib.rs:347-368
pub fn verify_transaction_inclusion_v2(&self, #[serializer(borsh)] args: ProofArgsV2) -> bool {
    require!(
        args.merkle_proof.len() == args.coinbase_merkle_proof.len(), ...
    );
    let header = self.headers_pool.get(&args.tx_block_blockhash)...;
    require!(
        merkle_tools::compute_root_from_merkle_proof(
            args.coinbase_tx_id.clone(), 0usize, &args.coinbase_merkle_proof,
        ) == header.block_header.merkle_root,
        "Incorrect coinbase merkle proof"
    );
    #[allow(deprecated)]
    self.verify_transaction_inclusion(args.into())   // <-- delegates to v1
}
```

However, v1 is still `pub` and carries only a Rust `#[deprecated]` attribute:

```rust
// contract/src/lib.rs:283-288
#[deprecated(since = "0.5.0", note = "Use `verify_transaction_inclusion_v2` instead.")]
#[pause]
pub fn verify_transaction_inclusion(&self, #[serializer(borsh)] args: ProofArgs) -> bool {
```

`#[deprecated]` is a **compiler-only lint**. It emits a warning to Rust callers but has zero effect on the compiled WASM binary or on NEAR's runtime dispatch. The method remains a fully accessible public entry point callable by any NEAR account with no additional restriction.

The contract's own documentation acknowledges the danger of calling v1 directly:

> **Warning**: This function may return `true` if the provided `tx_id` is a hash of an internal node in the Merkle tree rather than a valid transaction hash. [1](#0-0) 

The coinbase proof check in v2 is the only guard that prevents this forgery. Since v1 skips it entirely, any caller who invokes v1 directly receives the same `bool` result with none of the forgery protection. [2](#0-1) 

---

### Impact Explanation

The 64-byte transaction attack allows an attacker to craft a 64-byte input that is simultaneously a valid serialized internal Merkle tree node and a plausible "transaction hash." By supplying this crafted value as `tx_id` to `verify_transaction_inclusion` (v1), the attacker can make the function return `true` for a transaction that was never included in any Bitcoin block.

Any downstream NEAR contract or off-chain system that calls `verify_transaction_inclusion` (v1) to gate a privileged action — releasing bridged funds, minting tokens, confirming a cross-chain payment — can be deceived into accepting a forged proof. The broken invariant is: *the contract must not return `true` for a `tx_id` that is not a real leaf-level transaction hash in the claimed block*. v1 violates this invariant; v2 enforces it. Because v1 is still reachable, the invariant is not actually enforced at the contract boundary. [3](#0-2) 

---

### Likelihood Explanation

The entry path requires no privilege: any NEAR account can call `verify_transaction_inclusion` directly. The function is not gated by `#[trusted_relayer]`, role checks, or any access-control macro beyond `#[pause]`. The attacker only needs to know the method name (which is public ABI) and supply a crafted `ProofArgs`. The 64-byte forgery technique is well-documented and tooling exists to construct the required inputs. [4](#0-3) 

---

### Recommendation

Remove the `pub` visibility from `verify_transaction_inclusion` or restrict it to `pub(crate)` / `fn` so it is only callable internally (as it already is from `verify_transaction_inclusion_v2`). Alternatively, add the same coinbase proof validation directly inside v1, or gate v1 with an access-control role that prevents external callers from invoking it. Simply marking it `#[deprecated]` is insufficient because the NEAR runtime does not honour Rust deprecation attributes at dispatch time. [5](#0-4) 

---

### Proof of Concept

1. A target block `B` is on the mainchain. Its Merkle root is `R`.
2. The attacker identifies (or crafts) a 64-byte value `F` such that `SHA256d(F)` equals an internal Merkle node that hashes up to `R` with a short proof path `P`.
3. The attacker calls `verify_transaction_inclusion` directly (bypassing v2) with:
   - `tx_id = F`
   - `tx_block_blockhash = B`
   - `tx_index = <crafted position>`
   - `merkle_proof = P`
   - `confirmations = 0`
4. `compute_root_from_merkle_proof(F, index, P)` returns `R`, which equals `header.block_header.merkle_root`.
5. The function returns `true` — a forged proof accepted — without any coinbase validation having been performed.

The same call to `verify_transaction_inclusion_v2` would fail at the `require!` on line 358–365 because the attacker cannot supply a valid coinbase proof that also hashes to `R` via the internal-node trick. [6](#0-5) [7](#0-6)

### Citations

**File:** contract/src/lib.rs (L276-288)
```rust
    ///
    /// # Warning
    /// This function may return `true` if the provided `tx_id` is a hash of an internal node in the Merkle tree rather than a valid transaction hash.
    /// We assume that validation of whether the `tx_id` corresponds to a valid transaction hash is performed at a higher level of verification.
    ///
    /// # Panics
    /// Multiple cases
    #[deprecated(
        since = "0.5.0",
        note = "Use `verify_transaction_inclusion_v2` instead."
    )]
    #[pause]
    pub fn verify_transaction_inclusion(&self, #[serializer(borsh)] args: ProofArgs) -> bool {
```

**File:** contract/src/lib.rs (L315-323)
```rust
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
