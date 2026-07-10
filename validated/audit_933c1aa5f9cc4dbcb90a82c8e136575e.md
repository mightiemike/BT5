### Title
Deprecated `verify_transaction_inclusion` Remains Publicly Callable, Bypassing the Coinbase Merkle Proof Security Control — (File: `contract/src/lib.rs`)

---

### Summary

`verify_transaction_inclusion` (v1) is marked deprecated because it is vulnerable to the 64-byte transaction Merkle proof forgery attack. `verify_transaction_inclusion_v2` was introduced to close that gap by requiring a valid coinbase Merkle proof. However, v1 retains `pub` visibility and is still reachable by any unprivileged NEAR caller via direct RPC. Rust's `#[deprecated]` attribute is a compiler hint only; it does not restrict on-chain access. Any caller can therefore bypass the coinbase security layer entirely by invoking v1 directly, exactly as the ShapeShift snap bypassed MetaMask's security controls by accessing BIP32 entropy directly instead of going through the `endowment:ethereum-provider` endpoint.

---

### Finding Description

`verify_transaction_inclusion` is declared `pub` and annotated `#[pause]`, making it a first-class NEAR callable method: [1](#0-0) 

The function's own doc-comment acknowledges the vulnerability it carries: [2](#0-1) 

`verify_transaction_inclusion_v2` was introduced to fix this. Its only structural addition is a coinbase proof check before delegating back to v1: [3](#0-2) 

Because v1 is still `pub`, a caller can skip v2 entirely and call v1 directly. The `#[deprecated]` annotation produces a Rust compiler warning for crate-internal callers; it has zero effect on external NEAR RPC calls.

Inside v1, the only guard on the Merkle proof is a non-empty check: [4](#0-3) 

`compute_root_from_merkle_proof` is position-driven and accepts any starting hash: [5](#0-4) 

If an attacker supplies an **internal Merkle tree node** `N` at tree level `k` as `tx_id`, with a proof of length `D − k` (where `D` is the full tree depth), the function correctly walks up the remaining `D − k` levels and arrives at the stored Merkle root. The contract returns `true` for a hash that is not a leaf transaction.

---

### Impact Explanation

Any downstream NEAR contract or off-chain service that calls `verify_transaction_inclusion` to gate a privileged action (e.g., releasing bridged funds, minting wrapped tokens, confirming a cross-chain payment) can be deceived into accepting a forged proof. The attacker does not need to mine a block or control any privileged role; they only need a real Bitcoin block whose Merkle tree is publicly known. The corrupted state is the **proof result** returned to the caller: `true` is returned for a `tx_id` that was never a leaf transaction in the block.

---

### Likelihood Explanation

The entry path requires no special privilege. Any NEAR account can call `verify_transaction_inclusion` with a borsh-serialized `ProofArgs`. Bitcoin Merkle trees are fully public; computing an internal node and its sibling path is trivial with standard Bitcoin RPC (`getblock` with verbosity 2). The attack is deterministic and requires no brute-force search.

---

### Recommendation

Remove `pub` from `verify_transaction_inclusion` (make it `pub(crate)` or private) so it is no longer reachable via NEAR RPC. `verify_transaction_inclusion_v2` already calls it internally, so no external interface is lost. Alternatively, delete v1 entirely and inline its logic into v2. The goal is to ensure that the coinbase proof check is the **only** externally reachable code path for SPV verification, mirroring the recommendation in the ShapeShift report to route all operations through the secured provider endpoint rather than exposing the low-level primitive directly.

---

### Proof of Concept

1. Identify a Bitcoin block `B` already accepted by the contract (present in `mainchain_header_to_height`).
2. Fetch the full transaction list for `B` via Bitcoin RPC (`getblock <hash> 2`).
3. Build the Merkle tree locally. Pick any internal node `N` at level `k ≥ 1` (e.g., the parent of the first two leaf transactions). Record its position `p` at level `k` and its sibling path of length `D − k`.
4. Construct `ProofArgs`:
   - `tx_id` = `N` (the internal node hash)
   - `tx_block_blockhash` = hash of `B`
   - `tx_index` = `p` (position of `N` at level `k`)
   - `merkle_proof` = sibling path of length `D − k`
   - `confirmations` = 1
5. Call `verify_transaction_inclusion` via NEAR RPC with the borsh-encoded args.
6. The contract computes `compute_root_from_merkle_proof(N, p, proof)`, which correctly reconstructs the Merkle root, and returns **`true`** — falsely asserting that `N` is an included transaction in block `B`.

The v2 coinbase guard is never reached because the call goes directly to v1. [6](#0-5)

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
