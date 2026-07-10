### Title
Caller-Supplied `tx_id` Accepted as Authenticated Transaction Identity Without Verification — (`contract/src/lib.rs`)

### Summary

`verify_transaction_inclusion` accepts any caller-supplied 32-byte hash as `tx_id` and returns `true` if it satisfies the Merkle path computation, without verifying that the hash corresponds to a real transaction. An unprivileged NEAR caller can supply an internal Merkle tree node hash as `tx_id`, receive a `true` inclusion proof, and deceive any downstream contract or system that trusts the result. The function remains callable on-chain despite its `#[deprecated]` annotation.

### Finding Description

`verify_transaction_inclusion` is a public, access-control-free view function. Its only guard is `#[pause]`, which is inactive by default. [1](#0-0) 

The function accepts `args.tx_id` — a caller-supplied 32-byte hash — and passes it directly into the Merkle root computation: [2](#0-1) 

No check is performed to confirm that `tx_id` is a leaf node (i.e., a real transaction hash) rather than an internal Merkle tree node. The code itself documents this broken assumption: [3](#0-2) 

The `ProofArgs` struct carries `tx_id` as a plain `H256` with no type-level or runtime constraint distinguishing a transaction hash from an internal node: [4](#0-3) 

The `CLAUDE.md` confirms the vulnerability is present and reachable: [5](#0-4) 

The `#[deprecated]` annotation is a Rust compiler hint only — it imposes no runtime restriction. The function is still deployed and callable by any NEAR account.

### Impact Explanation

Any downstream contract or off-chain system that calls `verify_transaction_inclusion` and gates an action on its `true` return value (e.g., releasing bridged funds, confirming a cross-chain payment, unlocking collateral) can be deceived. An attacker with no privileged access can forge a passing inclusion proof for a transaction that does not exist, by substituting an internal Merkle node hash for a real transaction hash. The corrupted proof result is the exact state value that downstream consumers trust.

This is the direct analog to H-03: just as `get_public_key()` accepted any caller-supplied `PublicKey` header as an authenticated identity, `verify_transaction_inclusion` accepts any caller-supplied `tx_id` as an authenticated transaction identity — both without cryptographic ownership verification.

### Likelihood Explanation

Likelihood is high. The attack requires only:
1. A block on the main chain (public information).
2. The transaction hashes in that block (public, derivable from the Bitcoin RPC).
3. Computing the Merkle tree to obtain an internal node hash and its sibling path (trivial arithmetic).

No privileged keys, no leaked secrets, no social engineering. Any unprivileged NEAR account can call the function directly.

### Recommendation

1. **Remove `verify_transaction_inclusion` from the deployed contract** rather than merely deprecating it. A `#[deprecated]` tag does not prevent on-chain calls.
2. Enforce use of `verify_transaction_inclusion_v2` exclusively, which mitigates the second-preimage attack via the coinbase proof requirement.
3. If `verify_transaction_inclusion` must remain for backward compatibility, add a runtime guard that rejects calls (e.g., always panic with a clear migration message) so it cannot be exploited.

### Proof of Concept

**Setup**: A Bitcoin block on the main chain with transactions `[T0 (coinbase), T1, T2, T3]`. The Merkle tree has internal nodes `I_left = Hash(T0 || T1)` and `I_right = Hash(T2 || T3)`.

**Attack**:
```
near call <contract> verify_transaction_inclusion \
  --args-borsh ProofArgs {
    tx_id:              I_left,          // internal node, NOT a real tx
    tx_block_blockhash: <main-chain-block-hash>,
    tx_index:           0,               // position of I_left in the "virtual" tree
    merkle_proof:       [I_right],       // sibling needed to reach the root
    confirmations:      0,
  }
```

`compute_root_from_merkle_proof(I_left, 0, [I_right])` produces `Hash(I_left || I_right)` = the block's real Merkle root. The function returns `true`. No real transaction with hash `I_left` exists; the proof is forged entirely from public block data. [6](#0-5)

### Citations

**File:** contract/src/lib.rs (L278-280)
```rust
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

**File:** btc-types/src/contract_args.rs (L18-24)
```rust
pub struct ProofArgs {
    pub tx_id: H256,
    pub tx_block_blockhash: H256,
    pub tx_index: u64,
    pub merkle_proof: Vec<H256>,
    pub confirmations: u64,
}
```

**File:** contract/CLAUDE.md (L66-66)
```markdown
**Important**: This function is vulnerable to the standard Bitcoin merkle tree second-preimage attack — it may return `true` for an internal node hash rather than a real transaction hash. Block headers do not contain the transaction count, so proof depth cannot be validated on-chain. Callers MUST validate that the `tx_id` corresponds to a valid transaction (e.g., by verifying raw transaction data) before trusting the inclusion proof.
```
