### Title
Deprecated `verify_transaction_inclusion` Remains Publicly Callable, Enabling 64-Byte Merkle Proof Forgery — (File: `contract/src/lib.rs`)

---

### Summary

`verify_transaction_inclusion` (v1) is still a live, publicly callable NEAR contract method. It explicitly documents that it can return `true` when the caller supplies an internal Merkle tree node as `tx_id` instead of a real transaction hash. The `#[deprecated]` Rust attribute is a compiler-only warning with zero runtime enforcement. Any unprivileged NEAR caller can invoke v1 directly, bypassing the coinbase-proof guard added in v2, and receive a `true` verification result for a transaction that does not exist.

---

### Finding Description

`verify_transaction_inclusion` carries an explicit self-documenting warning:

> "This function may return `true` if the provided `tx_id` is a hash of an internal node in the Merkle tree rather than a valid transaction hash." [1](#0-0) 

The function is decorated with `#[deprecated]` and `#[pause]`, but neither attribute restricts who can call it at runtime. `#[deprecated]` is a Rust compiler lint — it emits a warning to Rust callers at compile time and has no effect on NEAR RPC dispatch. Any account can call the method by name over the NEAR RPC. [2](#0-1) 

The verification logic computes a Merkle root from the caller-supplied `tx_id`, `tx_index`, and `merkle_proof`, then compares it to the stored block header's `merkle_root`: [3](#0-2) 

`compute_root_from_merkle_proof` is a pure hash-chain computation with no constraint that `tx_id` must be a leaf node: [4](#0-3) 

**Concrete forgery path** — for a block whose Merkle tree has four transactions `[T0, T1, T2, T3]`:

| Merkle tree node | Value |
|---|---|
| `N_L` | `double_sha256(T0 ‖ T1)` |
| `N_R` | `double_sha256(T2 ‖ T3)` |
| `root` | `double_sha256(N_L ‖ N_R)` |

An attacker calls `verify_transaction_inclusion` with `tx_id = N_L`, `tx_index = 0`, `merkle_proof = [N_R]`. `compute_root_from_merkle_proof(N_L, 0, [N_R])` = `double_sha256(N_L ‖ N_R)` = `root`. The comparison succeeds and the function returns `true` for a transaction that does not exist.

`verify_transaction_inclusion_v2` was introduced to close this gap by requiring a coinbase proof of the same depth: [5](#0-4) 

But v2 does not remove or gate v1. Both methods are simultaneously live on the contract.

The `ProofArgs` struct accepted by v1 is fully attacker-controlled — `tx_id`, `tx_index`, `merkle_proof`, and `tx_block_blockhash` are all caller-supplied with no server-side binding: [6](#0-5) 

---

### Impact Explanation

Any downstream NEAR contract or bridge that calls `verify_transaction_inclusion` to gate a security-critical action (fund release, cross-chain message acceptance, collateral unlock) will accept a forged proof and authorize the action for a Bitcoin transaction that never existed. The corrupted value is the boolean proof result returned to the consumer — it is `true` when it must be `false`. This is a direct proof-verification forgery with concrete financial impact on any consumer of the v1 API.

**Impact: 4** — matches the external report's impact score; a successful forgery allows an attacker to claim arbitrary Bitcoin transactions occurred, enabling theft or unauthorized state transitions in any consumer contract.

---

### Likelihood Explanation

- The attack requires no privileged role, no leaked key, and no social engineering.
- The Merkle tree structure of any Bitcoin block is fully public data, derivable from any Bitcoin full node or block explorer.
- The 64-byte attack is well-known and explicitly referenced in the contract's own comments (line 267–268), meaning the attack surface is documented.
- The only precondition is that the contract is not paused (`#[pause]` guard), which is the normal operating state.

**Likelihood: 3** — the attacker needs only a NEAR account and public Bitcoin block data; the technique is documented in the codebase itself.

---

### Recommendation

1. **Remove or restrict v1 at the contract level.** Since `#[deprecated]` provides no runtime protection, the method should either be removed entirely or wrapped with an explicit `env::panic_str` that unconditionally rejects calls, forcing all callers to migrate to v2.

2. **Add a runtime guard.** If removal is not immediately possible, add `require!(false, "verify_transaction_inclusion is disabled; use verify_transaction_inclusion_v2")` as the first statement in v1's body.

3. **Audit all consumer contracts.** Any NEAR contract that calls `verify_transaction_inclusion` by name must be identified and migrated to `verify_transaction_inclusion_v2`.

---

### Proof of Concept

**Setup**: A confirmed mainchain block with hash `B` and Merkle root `R`, containing at least two transactions `T0` and `T1`, so that `N_L = double_sha256(T0 ‖ T1)` is a known internal node and `N_R = double_sha256(T2 ‖ T3)` (or the duplicate of `N_L` for a 2-tx block) satisfies `double_sha256(N_L ‖ N_R) = R`.

**Call** (NEAR CLI / RPC):
```bash
near call <contract_id> verify_transaction_inclusion \
  --args-borsh <borsh-encoded ProofArgs where:
      tx_id            = N_L   # internal node, not a real tx
      tx_block_blockhash = B
      tx_index         = 0
      merkle_proof     = [N_R]
      confirmations    = 1
  > \
  --accountId attacker.near
```

**Result**: The contract returns `true`. No real transaction with hash `N_L` exists in block `B`. Any consumer contract that trusted this result would proceed as if the transaction were confirmed. [7](#0-6)

### Citations

**File:** contract/src/lib.rs (L276-279)
```rust
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

**File:** btc-types/src/contract_args.rs (L16-24)
```rust
#[near(serializers = [borsh, json])]
#[derive(Clone, Debug)]
pub struct ProofArgs {
    pub tx_id: H256,
    pub tx_block_blockhash: H256,
    pub tx_index: u64,
    pub merkle_proof: Vec<H256>,
    pub confirmations: u64,
}
```
