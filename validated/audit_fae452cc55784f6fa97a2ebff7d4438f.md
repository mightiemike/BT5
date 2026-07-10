### Title
Deprecated `verify_transaction_inclusion` Accepts Caller-Supplied `tx_id` as an Internal Merkle Node, Enabling Proof Forgery — (File: `contract/src/lib.rs`)

---

### Summary

`BtcLightClient::verify_transaction_inclusion` remains callable by any unprivileged NEAR account despite being deprecated. It accepts a caller-supplied `tx_id` and returns `true` whenever `compute_root_from_merkle_proof(tx_id, tx_index, merkle_proof) == merkle_root`, with **no check that `tx_id` is a leaf-level transaction hash rather than an internal Merkle tree node**. An attacker can supply an internal node hash together with a valid sibling path and receive a `true` result for a transaction that was never broadcast or confirmed on Bitcoin.

---

### Finding Description

`verify_transaction_inclusion` (lines 283–323) is the direct, publicly callable entry point for SPV proof verification. Its own docstring acknowledges the flaw:

> *"This function may return `true` if the provided `tx_id` is a hash of an internal node in the Merkle tree rather than a valid transaction hash. We assume that validation of whether the `tx_id` corresponds to a valid transaction hash is performed at a higher level of verification."* [1](#0-0) 

The function is marked `#[deprecated]` but Rust's `deprecated` attribute is a **compile-time lint only**; it does not prevent runtime invocation. The function carries `#[pause]` but is not paused by default, so any NEAR account can call it directly. [2](#0-1) 

The entire verification body reduces to a single Merkle root comparison against the caller-supplied `tx_id`: [3](#0-2) 

`verify_transaction_inclusion_v2` was introduced to close this gap by requiring a coinbase proof at index 0, which constrains the tree structure and prevents internal-node substitution: [4](#0-3) 

However, the old function was never removed or gated, so the fix is trivially bypassed by calling `verify_transaction_inclusion` directly.

**Structural analog to the external report:** In the Securitize bridge, `_msgSender()` (a caller-controlled value) is encoded as the destination address without verifying the caller controls that address on the destination chain. Here, `args.tx_id` (a caller-controlled value) is used as the proof leaf without verifying it is a real transaction hash on the Bitcoin chain. In both cases the protocol accepts a caller-supplied identity as authoritative without an independent validity check, and downstream logic acts on the false positive.

---

### Impact Explanation

Any NEAR contract that calls `verify_transaction_inclusion` to gate a privileged action (token mint, bridge release, settlement) can be deceived. The attacker does not need to mine a block or forge a Bitcoin transaction. All required inputs — the block's Merkle tree structure and sibling hashes — are public data available from any Bitcoin full node or block explorer. A `true` return value from this function is the only on-chain signal those downstream contracts observe; they have no independent way to distinguish a leaf hash from an internal node hash.

---

### Likelihood Explanation

**Medium.** Every confirmed Bitcoin block's full Merkle tree is publicly reconstructible. The attacker needs only to:

1. Pick any mainchain block already stored in the contract.
2. Read its transaction list and compute the internal Merkle nodes.
3. Choose any internal node at depth D, set `tx_index` to the corresponding leaf-range index, and supply the D-level sibling path as `merkle_proof`.

No cryptographic work, no privileged access, and no special tooling beyond a Bitcoin RPC call are required.

---

### Recommendation

1. **Remove `verify_transaction_inclusion` from the public ABI.** Because `verify_transaction_inclusion_v2` supersedes it entirely, the old function should be deleted or converted to a private helper that is only reachable through the v2 path.
2. If backward compatibility is required, gate the old function behind a role (e.g., `Role::DAO`) so unprivileged callers cannot invoke it.
3. Document clearly in the contract README that any integrator must call `verify_transaction_inclusion_v2` exclusively.

---

### Proof of Concept

```
Setup:
  - Contract is initialized with real mainnet blocks 685440–685452 (already shown in tests).
  - Block 685452 is on the mainchain with a known merkle_root.

Attack:
  1. Fetch block 685452 from Bitcoin RPC: getblock <hash> 2
  2. Reconstruct the full Merkle tree from its transaction list.
  3. Pick the internal node N at depth 1, position 0
     (hash of the concatenation of tx[0] and tx[1]).
  4. The sibling path from position 0 at depth 1 to the root is a single
     hash: the right child of the root.
  5. Call verify_transaction_inclusion with:
       tx_id              = N   (internal node, not a real txid)
       tx_block_blockhash = hash of block 685452
       tx_index           = 0
       merkle_proof       = [right_child_of_root]
       confirmations      = 1
  6. compute_root_from_merkle_proof(N, 0, [right_child]) == merkle_root  → true
  7. The contract returns `true` for a transaction that does not exist.
``` [5](#0-4)

### Citations

**File:** contract/src/lib.rs (L277-279)
```rust
    /// # Warning
    /// This function may return `true` if the provided `tx_id` is a hash of an internal node in the Merkle tree rather than a valid transaction hash.
    /// We assume that validation of whether the `tx_id` corresponds to a valid transaction hash is performed at a higher level of verification.
```

**File:** contract/src/lib.rs (L283-288)
```rust
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
