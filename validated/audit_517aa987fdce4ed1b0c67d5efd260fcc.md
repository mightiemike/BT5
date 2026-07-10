### Title
Deprecated `verify_transaction_inclusion` Remains Directly Callable On-Chain, Enabling 64-Byte Merkle Proof Forgery — (`contract/src/lib.rs`)

---

### Summary

`verify_transaction_inclusion` (v1) is a public NEAR contract function that verifies Bitcoin transaction inclusion using only a Merkle path, without binding the proof to a real leaf node. The v2 replacement (`verify_transaction_inclusion_v2`) was introduced specifically to close the 64-byte internal-node forgery attack by requiring a coinbase proof of equal depth. However, Rust's `#[deprecated]` attribute is a compiler lint only — it does not prevent on-chain invocation. Any unprivileged NEAR caller can bypass the coinbase binding check entirely by calling v1 directly, obtaining a `true` result for a crafted internal Merkle node that is not a real transaction.

---

### Finding Description

`verify_transaction_inclusion_v2` enforces two checks before delegating to v1:

1. `merkle_proof.len() == coinbase_merkle_proof.len()` — ensures both proofs have the same depth.
2. `compute_root_from_merkle_proof(coinbase_tx_id, 0, &coinbase_merkle_proof) == header.block_header.merkle_root` — proves the coinbase (a real leaf at index 0) sits at the same tree depth as the claimed transaction. [1](#0-0) 

This depth-binding is the standard mitigation for the 64-byte attack: an internal Merkle node cannot be at the same depth as the coinbase leaf, so the equal-length requirement rejects it.

`verify_transaction_inclusion` (v1) performs none of these checks. It only computes:

```
compute_root_from_merkle_proof(tx_id, tx_index, &merkle_proof) == header.block_header.merkle_root
``` [2](#0-1) 

The function is marked `#[deprecated]` and carries an explicit warning that it "may return `true` if the provided `tx_id` is a hash of an internal node." But `#[deprecated]` in Rust is a compile-time lint — it has no runtime effect on a deployed WASM binary. The function is `pub`, carries only `#[pause]` (a runtime pause guard, not an access restriction), and is fully reachable by any NEAR account. [3](#0-2) 

The analog to the external report is direct: in the MechMarketplace bug, the request hash omitted the mech's address, so any mech could substitute itself. Here, the Merkle proof omits the coinbase depth binding, so any internal node can substitute for a real transaction. In both cases, a proof that should bind to a specific entity (a specific mech; a specific leaf node) fails to do so, and an adversary exploits the gap.

---

### Impact Explanation

Any downstream NEAR contract that calls `verify_transaction_inclusion` to gate a fund release, cross-chain unlock, or authorization decision receives a `true` result for a Bitcoin transaction that was never broadcast or confirmed. The corrupted value is the **proof result** itself. An attacker can fabricate "proof" of an arbitrary Bitcoin payment without ever making that payment on-chain, then trigger whatever action the consuming contract guards behind that proof.

---

### Likelihood Explanation

The attack requires no privileged access. The attacker needs only:
- Knowledge of a confirmed Bitcoin block's Merkle tree (all public data).
- The ability to call a NEAR contract function (any NEAR account).
- A target downstream contract that consumes `verify_transaction_inclusion` rather than `verify_transaction_inclusion_v2`.

The 64-byte attack is well-documented (Bitmex blog, CVE-2012-2459). The v1 function's own doc comment acknowledges the flaw. The only barrier is that a downstream consumer must be using v1 — which is realistic given that v1 predates v2 and integrators may not have migrated.

---

### Recommendation

Remove the callable body of `verify_transaction_inclusion` from the deployed contract. Replace it with an unconditional `env::panic_str("use verify_transaction_inclusion_v2")`, or remove the `pub` visibility so it is only reachable internally (from `verify_transaction_inclusion_v2`). The current pattern of marking it `#[deprecated]` while leaving it fully public provides no on-chain protection. [4](#0-3) 

---

### Proof of Concept

1. Identify a confirmed Bitcoin block `B` with a known Merkle tree of depth `D` (e.g., 8 transactions → depth 3).
2. Select an internal Merkle node `N` at depth `D-1` (a 32-byte hash that is the parent of two leaf transactions). This node is computable from public block data.
3. Construct a `merkle_proof` of length `D-1` that walks from `N` up to the Merkle root of `B`. Because `N` is a real internal node, `compute_root_from_merkle_proof(N, tx_index, proof)` will equal the block's `merkle_root`.
4. Call `verify_transaction_inclusion` with `tx_id = N`, `tx_block_blockhash = B`, `tx_index = <position>`, `merkle_proof = <constructed path>`, `confirmations = 1`.
5. The function returns `true`.
6. A downstream contract that calls `verify_transaction_inclusion` to verify a Bitcoin payment before releasing NEAR tokens now releases those tokens for a payment that never occurred on Bitcoin. [5](#0-4) [6](#0-5)

### Citations

**File:** contract/src/lib.rs (L283-288)
```rust
    #[deprecated(
        since = "0.5.0",
        note = "Use `verify_transaction_inclusion_v2` instead."
    )]
    #[pause]
    pub fn verify_transaction_inclusion(&self, #[serializer(borsh)] args: ProofArgs) -> bool {
```

**File:** contract/src/lib.rs (L317-323)
```rust
        // compute merkle tree root and check if it matches block's original merkle tree root
        merkle_tools::compute_root_from_merkle_proof(
            args.tx_id,
            usize::try_from(args.tx_index).unwrap(),
            &args.merkle_proof,
        ) == header.block_header.merkle_root
    }
```

**File:** contract/src/lib.rs (L348-365)
```rust
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
```

**File:** contract/src/lib.rs (L366-369)
```rust

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
