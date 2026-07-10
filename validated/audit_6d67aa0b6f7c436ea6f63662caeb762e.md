### Title
Arithmetic Underflow in `get_last_n_blocks_hashes` Panics on User-Controlled `skip`/`limit` Inputs - (File: contract/src/lib.rs)

### Summary

The public `get_last_n_blocks_hashes` function performs unchecked `u64` subtraction using caller-supplied `skip` and `limit` parameters. With `overflow-checks = true` enabled in the release profile, any call where `limit + skip > tip.block_height + 1` causes an arithmetic underflow panic, aborting the transaction and making the function permanently unreachable for those inputs.

### Finding Description

In `contract/src/lib.rs`, the function `get_last_n_blocks_hashes` computes a start height and a loop bound using raw `u64` subtraction on caller-controlled values:

```rust
let start_block_height =
    std::cmp::max(min_block_height, tip.block_height - limit - skip + 1);

for height in start_block_height..=(tip.block_height - skip) {
``` [1](#0-0) 

Both `tip.block_height - limit - skip + 1` (line 252) and `tip.block_height - skip` (line 254) are plain `u64` subtractions. If a caller passes `limit + skip >= tip.block_height + 1`, the first expression underflows before `std::cmp::max` can clamp it. If `skip > tip.block_height`, the loop bound on line 254 also underflows.

The `contract/Cargo.toml` release profile explicitly opts into overflow panics:

```toml
# Opt into extra safety checks on arithmetic operations
overflow-checks = true
``` [2](#0-1) 

All chain-specific profiles (`bitcoin`, `litecoin`, `zcash`, `dogecoin`) inherit from `release`, so the overflow check is active in every production build. [3](#0-2) 

### Impact Explanation

Any unprivileged NEAR caller invoking `get_last_n_blocks_hashes` with `limit` or `skip` values that exceed the current chain tip height causes the contract call to abort with an arithmetic overflow panic. The function is a public, unauthenticated view endpoint — no role, stake, or special permission is required. Callers that depend on this API (including the off-chain relayer's sync loop and any downstream NEAR contract consuming block-hash lists) receive a hard failure instead of a result. Because the underflow is triggered purely by the input values and the chain tip height is bounded by the actual chain length, a newly initialized contract with a small number of blocks is especially vulnerable — any `limit` larger than the stored block count triggers the panic.

### Likelihood Explanation

The trigger condition is trivially reachable: pass `limit = u64::MAX` or any value exceeding the current tip height. No privileged access, no special chain state, and no timing dependency is required. The relayer itself calls this function with a `limit` derived from the batch size; if the batch size ever exceeds the stored chain height (e.g., immediately after genesis initialization), the relayer's own call panics. An adversarial caller can also deliberately trigger this to disrupt any contract that wraps `get_last_n_blocks_hashes`.

### Recommendation

Replace the raw subtractions with checked or saturating arithmetic:

```rust
// Safe version
let start_block_height = std::cmp::max(
    min_block_height,
    tip.block_height
        .saturating_sub(limit)
        .saturating_sub(skip)
        .saturating_add(1),
);

let end_height = tip.block_height.saturating_sub(skip);
for height in start_block_height..=end_height {
    ...
}
```

This mirrors the pattern already used correctly elsewhere in the contract, such as the confirmation check in `verify_transaction_inclusion`:

```rust
(heaviest_block_header.block_height).saturating_sub(target_block_height) + 1
    >= args.confirmations,
``` [4](#0-3) 

### Proof of Concept

1. Deploy the contract with genesis at height 0 and `gc_threshold = 3`, submitting 12 initial blocks (tip height = 11 after init).
2. Call `get_last_n_blocks_hashes(skip=0, limit=12)`. This computes `11 - 12 - 0 + 1 = 0` — no underflow yet.
3. Call `get_last_n_blocks_hashes(skip=0, limit=13)`. This computes `11 - 13 - 0 + 1` on `u64`, which underflows to `u64::MAX - 1`. With `overflow-checks = true`, the WASM runtime aborts with an arithmetic overflow trap.
4. Call `get_last_n_blocks_hashes(skip=12, limit=0)`. This computes `tip.block_height - skip = 11 - 12` on line 254, which also underflows and panics.

The function is declared at: [5](#0-4)

### Citations

**File:** contract/src/lib.rs (L237-261)
```rust
    pub fn get_last_n_blocks_hashes(&self, skip: u64, limit: u64) -> Vec<H256> {
        let mut block_hashes = vec![];
        let tip_hash = &self.mainchain_tip_blockhash;
        let tip = self
            .headers_pool
            .get(tip_hash)
            .unwrap_or_else(|| env::panic_str("heaviest block should be recorded"));

        let min_block_height = self
            .headers_pool
            .get(&self.mainchain_initial_blockhash)
            .unwrap_or_else(|| env::panic_str("initial block should be recorded"))
            .block_height;

        let start_block_height =
            std::cmp::max(min_block_height, tip.block_height - limit - skip + 1);

        for height in start_block_height..=(tip.block_height - skip) {
            if let Some(block_hash) = self.mainchain_height_to_header.get(&height) {
                block_hashes.push(block_hash);
            }
        }

        block_hashes
    }
```

**File:** contract/src/lib.rs (L304-308)
```rust
        require!(
            (heaviest_block_header.block_height).saturating_sub(target_block_height) + 1
                >= args.confirmations,
            "Not enough blocks confirmed"
        );
```

**File:** contract/Cargo.toml (L72-73)
```text
# Opt into extra safety checks on arithmetic operations https://stackoverflow.com/a/64136471/249801
overflow-checks = true
```

**File:** contract/Cargo.toml (L75-85)
```text
[profile.bitcoin]
inherits = "release"

[profile.litecoin]
inherits = "release"

[profile.zcash]
inherits = "release"

[profile.dogecoin]
inherits = "release"
```
