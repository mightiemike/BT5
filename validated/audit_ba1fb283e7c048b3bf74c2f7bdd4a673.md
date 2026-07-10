### Title
Caller-Supplied `confirmations = 0` Bypasses Reorganization-Safety Guarantee in SPV Proof Verification — (`contract/src/lib.rs`)

---

### Summary

Both `verify_transaction_inclusion` and `verify_transaction_inclusion_v2` accept a caller-controlled `confirmations: u64` field with only an **upper-bound** check (`<= gc_threshold`) and no lower-bound check. Passing `confirmations = 0` makes the confirmation guard trivially true for any on-chain block, completely bypassing the reorganization-safety invariant the parameter is designed to enforce.

---

### Finding Description

`ProofArgs` and `ProofArgsV2` both carry a `confirmations: u64` field that is fully caller-controlled. [1](#0-0) [2](#0-1) 

Inside `verify_transaction_inclusion`, the only validation applied to `confirmations` is an upper-bound guard: [3](#0-2) 

The actual confirmation-depth check that follows is: [4](#0-3) 

When `confirmations = 0`, the expression `(tip_height - target_height + 1) >= 0` is always `true` for any `u64` arithmetic, so the guard never fires. The function then proceeds to evaluate the Merkle proof and can return `true` for a transaction whose block is the current chain tip — i.e., a transaction with **zero confirmations**.

`verify_transaction_inclusion_v2` delegates to v1 after its own coinbase-proof check, so it inherits the same flaw: [5](#0-4) 

The `From<ProofArgsV2> for ProofArgs` conversion passes `confirmations` through unchanged: [6](#0-5) 

---

### Impact Explanation

The `confirmations` parameter exists specifically to protect against block reorganizations: a recipient contract should not release funds until a Bitcoin transaction is buried under enough proof-of-work. With `confirmations = 0`, that protection is entirely absent. A caller can:

1. Submit a Bitcoin block containing transaction T (valid PoW required, but no extra privilege).
2. Immediately call `verify_transaction_inclusion_v2` with `confirmations = 0` and a valid Merkle proof.
3. Receive `true` even though T has zero confirmations and the block could still be reorganized away.

Any NEAR contract that consumes the boolean result and releases assets (tokens, unlocks, etc.) based on it is vulnerable to a double-spend or premature-release attack if the `confirmations` value is not independently enforced by the consuming contract.

---

### Likelihood Explanation

The entry path requires no privileged role — any NEAR account can call `verify_transaction_inclusion` or `verify_transaction_inclusion_v2` directly. The attacker only needs to supply a structurally valid `ProofArgs` with `confirmations = 0`. No leaked keys, social engineering, or external dependency failure is required. The attack is straightforward and deterministic.

---

### Recommendation

Add a minimum-confirmations guard immediately after the existing upper-bound check:

```rust
require!(
    args.confirmations >= 1,
    "Confirmations must be at least 1"
);
require!(
    args.confirmations <= self.gc_threshold,
    "The required number of confirmations exceeds the number of blocks stored in memory"
);
```

Alternatively, enforce a protocol-level minimum (e.g., 6 for Bitcoin mainnet) and reject any value below it, making the parameter a "minimum requested" rather than a freely chosen bypass.

---

### Proof of Concept

```
1. Relayer submits block B containing transaction T (valid PoW, valid Merkle root).
   Block B becomes the mainchain tip at height H.

2. Attacker calls verify_transaction_inclusion_v2 with:
     tx_id                = hash(T)
     tx_block_blockhash   = hash(B)
     tx_index             = <correct index>
     merkle_proof         = <valid proof for T in B>
     coinbase_tx_id       = hash(coinbase of B)
     coinbase_merkle_proof= <valid coinbase proof>
     confirmations        = 0          ← no lower-bound check exists

3. Inside verify_transaction_inclusion:
     require!(0 <= gc_threshold)       → passes (upper-bound only)
     require!((H - H) + 1 >= 0)       → passes (always true for u64)
     Merkle root computed == stored    → passes (proof is valid)
   → returns true

4. Recipient contract releases funds based on the true result,
   before T has a single confirmation.
   A subsequent reorg that drops block B invalidates T retroactively.
``` [7](#0-6)

### Citations

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

**File:** btc-types/src/contract_args.rs (L27-36)
```rust
#[derive(Clone, Debug)]
pub struct ProofArgsV2 {
    pub tx_id: H256,
    pub tx_block_blockhash: H256,
    pub tx_index: u64,
    pub merkle_proof: Vec<H256>,
    pub coinbase_tx_id: H256,
    pub coinbase_merkle_proof: Vec<H256>,
    pub confirmations: u64,
}
```

**File:** btc-types/src/contract_args.rs (L38-47)
```rust
impl From<ProofArgsV2> for ProofArgs {
    fn from(args: ProofArgsV2) -> Self {
        Self {
            tx_id: args.tx_id,
            tx_block_blockhash: args.tx_block_blockhash,
            tx_index: args.tx_index,
            merkle_proof: args.merkle_proof,
            confirmations: args.confirmations,
        }
    }
```

**File:** contract/src/lib.rs (L288-323)
```rust
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

**File:** contract/src/lib.rs (L367-369)
```rust
        #[allow(deprecated)]
        self.verify_transaction_inclusion(args.into())
    }
```
