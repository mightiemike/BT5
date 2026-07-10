### Title
Caller-Supplied `confirmations = 0` Bypasses All Confirmation-Depth Security in `verify_transaction_inclusion` — (`contract/src/lib.rs`)

---

### Summary

The `verify_transaction_inclusion` and `verify_transaction_inclusion_v2` functions accept a fully caller-controlled `confirmations` parameter with **no minimum value enforced**. Passing `confirmations = 0` trivially satisfies the depth check for every block on the main chain, including the chain tip, making the proof result meaningless as a security guarantee against chain reorganizations. This is the direct analog of the Radiant slippage-tolerance class: a user-configurable validation threshold with no lower-bound enforcement, allowing the protection to be silently zeroed out.

---

### Finding Description

`verify_transaction_inclusion` enforces only an **upper** bound on `confirmations`:

```rust
require!(
    args.confirmations <= self.gc_threshold,
    "The required number of confirmations exceeds the number of blocks stored in memory"
);
``` [1](#0-0) 

The subsequent depth check is:

```rust
require!(
    (heaviest_block_header.block_height).saturating_sub(target_block_height) + 1
        >= args.confirmations,
    "Not enough blocks confirmed"
);
``` [2](#0-1) 

Because `confirmations` is a `u64`, passing `0` makes the right-hand side `0`, and the expression `(height_diff + 1) >= 0` is **always true** for any unsigned integer. The check becomes a no-op: every block on the main chain, including the very tip, passes unconditionally.

`verify_transaction_inclusion_v2` delegates to the same function after its coinbase-proof check, so it inherits the identical flaw:

```rust
#[allow(deprecated)]
self.verify_transaction_inclusion(args.into())
``` [3](#0-2) 

The `ProofArgs` and `ProofArgsV2` structs place no constraint on `confirmations` at the type level:

```rust
pub struct ProofArgs {
    ...
    pub confirmations: u64,
}
``` [4](#0-3) 

---

### Impact Explanation

A consumer contract (bridge, atomic-swap protocol, cross-chain lending layer) that calls `verify_transaction_inclusion` with `confirmations = 0` receives `true` for any transaction in any mainchain block, including the block at the current tip. Because the tip block has received zero additional confirmations, it is maximally vulnerable to a chain reorganization. An attacker can:

1. Broadcast a Bitcoin transaction and wait for it to appear in the next block submitted by the relayer.
2. Immediately call `verify_transaction_inclusion` with `confirmations = 0` against that tip block.
3. The contract returns `true`.
4. The consumer releases funds or executes a cross-chain action.
5. The attacker simultaneously mines a competing Bitcoin chain branch that excludes (double-spends) the original transaction.
6. Once the competing branch accumulates more chainwork, the relayer submits it and the contract reorgs; the original block is evicted from the mainchain maps.
7. The consumer has already acted on a proof that is now invalid.

The corrupted proof result is concrete: `verify_transaction_inclusion` returns `true` for a transaction whose containing block is subsequently removed from `mainchain_header_to_height` during a reorg.

---

### Likelihood Explanation

The entry path is fully unprivileged: `verify_transaction_inclusion` and `verify_transaction_inclusion_v2` are public view functions callable by any NEAR account or contract. No staking, role, or deposit is required. The `confirmations` field is a plain integer in the `ProofArgs` borsh struct — any caller can set it to `0`. Consumer contracts that forward a user-supplied confirmation count (e.g., to let users choose speed vs. safety, mirroring the Radiant slippage-tolerance pattern) are directly exploitable. Even well-intentioned consumers may default to `0` during development or testing and ship that value to production.

---

### Recommendation

Enforce a **minimum confirmation count** inside `verify_transaction_inclusion`, analogous to the 5% maximum slippage cap introduced in the Radiant fix:

```rust
const MIN_CONFIRMATIONS: u64 = 1; // or a protocol-defined constant (e.g., 6)

require!(
    args.confirmations >= MIN_CONFIRMATIONS,
    "Confirmations must be at least MIN_CONFIRMATIONS"
);
require!(
    args.confirmations <= self.gc_threshold,
    "The required number of confirmations exceeds the number of blocks stored in memory"
);
```

Because `verify_transaction_inclusion_v2` delegates to `verify_transaction_inclusion`, a single fix at the inner function covers both entry points. The minimum value should be documented and ideally configurable by the DAO (with its own lower bound), so that different consumer risk profiles can be accommodated without allowing the protection to be zeroed out.

---

### Proof of Concept

```
# 1. Relayer submits the latest Bitcoin block (height H) to the contract.
#    mainchain_tip_blockhash now points to block H.

# 2. Attacker calls verify_transaction_inclusion with:
#      tx_id            = <tx hash in block H>
#      tx_block_blockhash = <hash of block H>
#      tx_index         = <index of tx>
#      merkle_proof     = <valid Merkle path>
#      confirmations    = 0          ← attacker-controlled

# 3. Contract evaluation:
#    require!(0 <= gc_threshold)          → passes (0 ≤ any positive u64)
#    depth = (H - H) + 1 = 1
#    require!(1 >= 0)                     → passes unconditionally

# 4. Contract returns `true`.

# 5. Consumer contract releases funds / executes cross-chain action.

# 6. Attacker broadcasts a competing Bitcoin branch that omits the tx.
#    Relayer submits the competing branch; contract reorgs block H out.
#    The "verified" transaction no longer exists on the canonical chain.
```

The root cause — absence of a lower-bound `require` on `confirmations` — is at: [5](#0-4)

### Citations

**File:** contract/src/lib.rs (L289-308)
```rust
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
```

**File:** contract/src/lib.rs (L367-368)
```rust
        #[allow(deprecated)]
        self.verify_transaction_inclusion(args.into())
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
