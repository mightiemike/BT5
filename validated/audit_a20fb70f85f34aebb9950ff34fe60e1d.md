### Title
Missing Lower-Bound Validation on `gc_threshold` Permanently Breaks SPV Verification — (File: `contract/src/lib.rs`)

---

### Summary

The `gc_threshold` system parameter is accepted during contract initialization without any lower-bound validation. If set to `0`, both `verify_transaction_inclusion` and `verify_transaction_inclusion_v2` permanently reject every call that specifies `confirmations >= 1`, making the contract's primary SPV function permanently unusable. Because no setter exists for `gc_threshold`, the broken state cannot be corrected without a full contract upgrade.

---

### Finding Description

`gc_threshold` is stored as a plain `u64` field and is written directly from the caller-supplied `InitArgs` struct with no range check: [1](#0-0) 

The field's intended semantic is "minimum number of blocks to keep in storage." The recommended value is `52704` (one year of Bitcoin blocks). A value of `0` is semantically incoherent — it would mean "keep zero blocks" — but the contract accepts it silently.

`verify_transaction_inclusion` enforces the following guard before doing any proof work: [2](#0-1) 

With `gc_threshold = 0`, the condition `args.confirmations <= 0` is `false` for every `confirmations >= 1`. The `require!` macro calls `env::panic_str`, aborting the transaction. Because `gc_threshold` has no public setter, this state is permanent.

`verify_transaction_inclusion_v2` delegates to `verify_transaction_inclusion` after its own coinbase-proof check: [3](#0-2) 

Both entry points are therefore permanently broken.

The GC logic in `run_mainchain_gc` is also affected: with `gc_threshold = 0`, `total_amount_to_remove` equals the entire stored chain on every invocation, causing the GC to strip historical headers aggressively and further undermining any future attempt to serve proofs even if the confirmation guard were bypassed: [4](#0-3) 

---

### Impact Explanation

The contract's sole security-relevant output is the result of `verify_transaction_inclusion` / `verify_transaction_inclusion_v2`. Downstream NEAR contracts that consume this result to gate asset releases or cross-chain actions will receive a permanent panic instead of a boolean. With `gc_threshold = 0`, the only non-panicking call is `confirmations = 0`, which provides zero security (it accepts a transaction the moment it appears in any block, with no PoW depth). The SPV bridge is rendered permanently non-functional for any production confirmation threshold.

---

### Likelihood Explanation

The `init` function is `#[private]` (deployer-only), so the misconfiguration requires an operator mistake rather than an external attacker. However, the `InitArgs` struct exposes `gc_threshold` as a plain integer with no documentation of a required minimum in the struct definition itself: [5](#0-4) 

The only guidance is a code comment in `lib.rs` recommending `52704`. A deployer testing with a minimal or zero value — common during staging — would silently produce a broken production contract. Once deployed, there is no recovery path short of a contract upgrade, matching the "immutable after erroneous configuration" property of the original Sai bug.

---

### Recommendation

Add an explicit lower-bound guard inside `init` before writing `gc_threshold` to state:

```rust
require!(
    args.gc_threshold >= 1,
    "gc_threshold must be at least 1"
);
```

For defense-in-depth, also add a minimum meaningful floor (e.g., `>= 144`, one day of blocks) so that the GC cannot be configured to a value that makes confirmation-based SPV semantically impossible.

---

### Proof of Concept

1. Deploy the contract with `InitArgs { gc_threshold: 0, … }`.
2. Submit enough initial blocks to satisfy the `MEDIAN_TIME_SPAN` requirement.
3. Call `verify_transaction_inclusion` with `confirmations: 1`.
4. The contract panics: `"The required number of confirmations exceeds the number of blocks stored in memory"`.
5. Repeat with any `confirmations >= 1` — every call panics.
6. Attempt to call `verify_transaction_inclusion` with `confirmations: 0` — it succeeds, but provides no security guarantee (zero-confirmation acceptance).
7. There is no on-chain call that can change `gc_threshold`; the contract is permanently broken for production SPV use.

### Citations

**File:** contract/src/lib.rs (L142-144)
```rust
            skip_pow_verification: args.skip_pow_verification,
            gc_threshold: args.gc_threshold,
            network: args.network,
```

**File:** contract/src/lib.rs (L289-292)
```rust
        require!(
            args.confirmations <= self.gc_threshold,
            "The required number of confirmations exceeds the number of blocks stored in memory"
        );
```

**File:** contract/src/lib.rs (L367-369)
```rust
        #[allow(deprecated)]
        self.verify_transaction_inclusion(args.into())
    }
```

**File:** contract/src/lib.rs (L391-393)
```rust
        if amount_of_headers_we_store > self.gc_threshold {
            let total_amount_to_remove = amount_of_headers_we_store - self.gc_threshold;
            let selected_amount_to_remove = std::cmp::min(total_amount_to_remove, batch_size);
```

**File:** relayer/src/config.rs (L50-57)
```rust
#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct InitConfig {
    pub network: Network,
    pub num_of_blcoks_to_submit: u64,
    pub gc_threshold: u64,
    pub skip_pow_verification: bool,
    pub init_height: u64,
}
```
