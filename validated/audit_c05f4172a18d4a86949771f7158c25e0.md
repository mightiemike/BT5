### Title
Missing Non-Zero Validation for `gc_threshold` in `init` Permanently Breaks Transaction Verification - (File: `contract/src/lib.rs`)

---

### Summary

The `init` function of `BtcLightClient` accepts `args.gc_threshold` without validating it is non-zero. If the contract is deployed with `gc_threshold = 0`, both `verify_transaction_inclusion` and `verify_transaction_inclusion_v2` permanently panic for any caller requesting even one confirmation, and `run_mainchain_gc` aggressively removes all stored headers on every `submit_blocks` call, corrupting `mainchain_initial_blockhash` into a state that causes further panics. Because `#[init]` can only be called once, there is no recovery path without a privileged migration.

---

### Finding Description

In `contract/src/lib.rs`, the `init` function stores `args.gc_threshold` directly into contract state with no lower-bound guard:

```rust
// contract/src/lib.rs L135-L145
pub fn init(args: InitArgs) -> Self {
    let mut contract = Self {
        ...
        gc_threshold: args.gc_threshold,   // ← no require!(args.gc_threshold > 0)
        ...
    };
``` [1](#0-0) 

`gc_threshold` is the sole bound used in two places:

**1. Verification gate** — `verify_transaction_inclusion` (called directly and via `verify_transaction_inclusion_v2`) opens with:

```rust
// L289-L292
require!(
    args.confirmations <= self.gc_threshold,
    "The required number of confirmations exceeds the number of blocks stored in memory"
);
``` [2](#0-1) 

With `gc_threshold = 0`, any caller passing `confirmations >= 1` (the only meaningful value) hits this `require!` and the call panics unconditionally.

**2. GC loop** — `run_mainchain_gc` triggers whenever `amount_of_headers_we_store > self.gc_threshold`:

```rust
// L391-L414
if amount_of_headers_we_store > self.gc_threshold {
    let total_amount_to_remove = amount_of_headers_we_store - self.gc_threshold;
    ...
    self.mainchain_initial_blockhash = self
        .mainchain_height_to_header
        .get(&end_removal_height)
        .unwrap_or_else(|| env::panic_str(ERR_KEY_NOT_EXIST));
}
``` [3](#0-2) 

With `gc_threshold = 0`, `amount_of_headers_we_store > 0` is always true (genesis block is always present). Every `submit_blocks` call triggers GC, removes all headers down to height 0, then attempts to set `mainchain_initial_blockhash` to the block at `end_removal_height` — a slot that was just deleted — causing a panic at line 413.

`run_mainchain_gc` is called automatically inside `submit_blocks`:

```rust
// L181
self.run_mainchain_gc(num_of_headers);
``` [4](#0-3) 

So every relayer submission after genesis also panics, halting chain synchronization entirely.

---

### Impact Explanation

With `gc_threshold = 0` set at init time:

- **`verify_transaction_inclusion` / `verify_transaction_inclusion_v2`**: permanently panic for any `confirmations >= 1`. The contract's primary purpose — trustless cross-chain transaction verification — is completely non-functional from the first block onward.
- **`submit_blocks`**: panics on every call after genesis due to GC attempting to read a deleted key, halting all chain synchronization.
- **State is permanently corrupted**: `#[init]` is a one-shot function; there is no re-initialization path. Recovery requires a privileged `migrate` call, which itself requires the DAO role to have been granted beforehand.

The corrupted invariant is concrete: `gc_threshold` is the authoritative upper bound on stored block depth and the confirmation-count gate. Setting it to zero violates both invariants simultaneously and irrecoverably.

---

### Likelihood Explanation

`gc_threshold` is a plain `u64` with no documented minimum in `InitArgs`. A deployer who omits the field (defaulting to `0` in JSON), passes `"gc_threshold": 0` by mistake, or uses a misconfigured relayer `InitConfig` (the relayer's `init_contract` path passes `gc_threshold` from config directly) would silently deploy a permanently broken contract. The relayer's `init_contract` path passes `gc_threshold` from config with no validation either. [5](#0-4) 

---

### Recommendation

Add a non-zero (and preferably minimum-value) guard at the top of `init`:

```rust
require!(args.gc_threshold > 0, "gc_threshold must be non-zero");
// Optionally: require!(args.gc_threshold >= MINIMUM_GC_THRESHOLD, "gc_threshold too small");
```

The recommended value from the contract's own documentation is `52704` (approximately one year of Bitcoin blocks). Enforcing a sensible minimum at init time prevents permanent misconfiguration.

---

### Proof of Concept

1. Deploy the contract with `InitArgs { gc_threshold: 0, ... }` (all other fields valid).
2. `init` succeeds — no guard rejects the zero value.
3. Call `verify_transaction_inclusion` with `confirmations: 1`:
   - `require!(1 <= 0, ...)` → **panics** with `"The required number of confirmations exceeds the number of blocks stored in memory"`.
4. Call `submit_blocks` with any valid header:
   - `run_mainchain_gc(1)` is called; `amount_of_headers_we_store (≥1) > 0` → GC fires.
   - All headers from genesis height to `end_removal_height` are deleted.
   - `mainchain_height_to_header.get(&end_removal_height)` returns `None` → **panics** with `ERR_KEY_NOT_EXIST`.
5. The contract is permanently non-functional. No unprivileged caller can recover it.

### Citations

**File:** contract/src/lib.rs (L135-145)
```rust
    pub fn init(args: InitArgs) -> Self {
        let mut contract = Self {
            mainchain_height_to_header: LookupMap::new(StorageKey::MainchainHeightToHeader),
            mainchain_header_to_height: LookupMap::new(StorageKey::MainchainHeaderToHeight),
            headers_pool: LookupMap::new(StorageKey::HeadersPool),
            mainchain_initial_blockhash: H256::default(),
            mainchain_tip_blockhash: H256::default(),
            skip_pow_verification: args.skip_pow_verification,
            gc_threshold: args.gc_threshold,
            network: args.network,
        };
```

**File:** contract/src/lib.rs (L181-181)
```rust
        self.run_mainchain_gc(num_of_headers);
```

**File:** contract/src/lib.rs (L289-292)
```rust
        require!(
            args.confirmations <= self.gc_threshold,
            "The required number of confirmations exceeds the number of blocks stored in memory"
        );
```

**File:** contract/src/lib.rs (L391-414)
```rust
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
```

**File:** btc-types/src/contract_args.rs (L7-14)
```rust
pub struct InitArgs {
    pub genesis_block_hash: H256,
    pub genesis_block_height: u64,
    pub skip_pow_verification: bool,
    pub gc_threshold: u64,
    pub network: Network,
    pub submit_blocks: Vec<Header>,
}
```
