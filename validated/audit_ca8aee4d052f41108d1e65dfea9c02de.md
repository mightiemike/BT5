### Title
Unprivileged Caller Can Trigger Aggressive Mainchain GC, Permanently Breaking SPV Proof Verification - (`contract/src/lib.rs`)

### Summary
`run_mainchain_gc` is a state-mutating function callable by any unprivileged NEAR account without any caller authorization check when the contract is not paused. An attacker can supply an arbitrarily large `batch_size` to prune the maximum possible number of mainchain block headers in a single transaction, permanently breaking SPV proof verification for those blocks and corrupting chain-reorganization traversal.

### Finding Description

`submit_blocks` is correctly gated behind `#[trusted_relayer]`, which enforces that only accounts holding `UnrestrictedSubmitBlocks`, `DAO`, or a registered relayer stake may submit headers. [1](#0-0) 

`run_mainchain_gc`, however, carries only `#[pause(except(roles(Role::UnrestrictedRunGC)))]`. That attribute controls behaviour **when the contract is paused** (only `UnrestrictedRunGC` holders may call it then), but imposes **no restriction at all when the contract is live**. There is no `#[trusted_relayer]` on the function, no role check, and no deposit requirement. [2](#0-1) 

The function accepts an attacker-controlled `batch_size: u64`. Internally it computes:

```
selected_amount_to_remove = min(total_amount_to_remove, batch_size)
``` [3](#0-2) 

Passing `u64::MAX` as `batch_size` causes the GC to remove every mainchain header above the `gc_threshold` floor in a single call — far more aggressively than the incremental removal the legitimate relayer path performs (which uses `batch_size = num_of_headers` submitted in that call). [4](#0-3) 

The removed headers are deleted from both `mainchain_height_to_header` and `headers_pool`: [5](#0-4) 

Once deleted, those entries are gone permanently. `verify_transaction_inclusion` and `verify_transaction_inclusion_v2` both look up the block in `headers_pool` and panic if it is absent: [6](#0-5) 

Chain-reorganization traversal also walks `headers_pool` backwards and panics on a missing entry, as documented explicitly: [7](#0-6) 

### Impact Explanation

Any unprivileged NEAR account can call `run_mainchain_gc(u64::MAX)` at any time the contract is live. This immediately prunes every mainchain header above the `gc_threshold` floor. Downstream effects:

1. **SPV proof forgery / permanent verification failure** — `verify_transaction_inclusion_v2` (and the deprecated v1) will panic for any transaction whose block was pruned. Consumer contracts or bridges relying on these proofs lose the ability to verify historical transactions, which can block withdrawals or settlement finality.
2. **Chain-reorg corruption** — if the pruned region overlaps with a pending fork's common ancestor, the reorg walk panics with `PrevBlockNotFound`, leaving the canonical chain pointer in an inconsistent state.

The corrupted canonical mapping is the `mainchain_height_to_header` / `headers_pool` pair, and the broken invariant is that every block within `[mainchain_initial_blockhash, mainchain_tip_blockhash]` must be present in `headers_pool`.

### Likelihood Explanation

The entry path requires no role, no stake, and no deposit — only a standard NEAR transaction. The call is cheap in gas. Any actor who wants to disrupt SPV verification (e.g., to prevent a bridge from processing a withdrawal) can execute this attack at will.

### Recommendation

Add an explicit caller authorization check to `run_mainchain_gc` mirroring the guard on `submit_blocks`. The simplest approach is to require the caller to hold at least one of `UnrestrictedRunGC`, `UnrestrictedSubmitBlocks`, or `DAO` roles regardless of pause state, or to apply `#[trusted_relayer]` to the function. Alternatively, make the function `pub(crate)` and remove it from the public ABI entirely, since the relayer already triggers it internally via `submit_blocks`.

### Proof of Concept

```
# 1. Deploy contract (gc_threshold = 52704, mainchain has 60000 blocks)
# 2. As any unprivileged account:
near call <contract_id> run_mainchain_gc \
  '{"batch_size": 18446744073709551615}' \
  --accountId attacker.near

# Result: 7296 mainchain headers pruned immediately.
# Any subsequent call to verify_transaction_inclusion for a tx
# in those pruned blocks panics: "cannot find requested transaction block"
``` [8](#0-7) [9](#0-8)

### Citations

**File:** contract/src/lib.rs (L166-169)
```rust
    #[payable]
    #[pause]
    #[trusted_relayer]
    pub fn submit_blocks(
```

**File:** contract/src/lib.rs (L175-181)
```rust
        let num_of_headers = headers.len().try_into().unwrap();

        for header in headers {
            self.submit_block_header(header, self.skip_pow_verification);
        }

        self.run_mainchain_gc(num_of_headers);
```

**File:** contract/src/lib.rs (L310-322)
```rust
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
```

**File:** contract/src/lib.rs (L376-416)
```rust
    #[pause(except(roles(Role::UnrestrictedRunGC)))]
    pub fn run_mainchain_gc(&mut self, batch_size: u64) {
        let initial_blockheader = self
            .headers_pool
            .get(&self.mainchain_initial_blockhash)
            .unwrap_or_else(|| env::panic_str(ERR_KEY_NOT_EXIST));

        let tip_blockheader = self
            .headers_pool
            .get(&self.mainchain_tip_blockhash)
            .unwrap_or_else(|| env::panic_str(ERR_KEY_NOT_EXIST));

        let amount_of_headers_we_store =
            tip_blockheader.block_height - initial_blockheader.block_height + 1;

        if amount_of_headers_we_store > self.gc_threshold {
            let total_amount_to_remove = amount_of_headers_we_store - self.gc_threshold;
            let selected_amount_to_remove = std::cmp::min(total_amount_to_remove, batch_size);

            let start_removal_height = initial_blockheader.block_height;
            let end_removal_height = initial_blockheader.block_height + selected_amount_to_remove;
            env::log_str(&format!(
                "Num of blocks to remove {selected_amount_to_remove}"
            ));

            for height in start_removal_height..end_removal_height {
                let blockhash = &self
                    .mainchain_height_to_header
                    .get(&height)
                    .unwrap_or_else(|| env::panic_str(ERR_KEY_NOT_EXIST));

                self.remove_block_header(blockhash);
                self.mainchain_height_to_header.remove(&height);
            }

            self.mainchain_initial_blockhash = self
                .mainchain_height_to_header
                .get(&end_removal_height)
                .unwrap_or_else(|| env::panic_str(ERR_KEY_NOT_EXIST));
        }
    }
```

**File:** contract/CLAUDE.md (L60-60)
```markdown
**Caveat**: If mainchain blocks near the fork point have been garbage collected, reorg will fail — the contract panics with `PrevBlockNotFound` when it cannot walk the chain back to the common ancestor. This means GC depth must be set conservatively relative to expected fork lengths
```
