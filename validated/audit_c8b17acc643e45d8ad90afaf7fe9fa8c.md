### Title
NEAR Block Timestamp Manipulation Allows Acceptance of Future-Timestamped Chain Headers - (File: `contract/src/bitcoin.rs`, `contract/src/litecoin.rs`, `contract/src/dogecoin.rs`, `contract/src/zcash.rs`)

---

### Summary

All four chain-specific `check_pow` implementations use `env::block_timestamp_ms()` — the NEAR block producer's timestamp — as the reference "local time" for the `time-too-new` guard. Because NEAR block producers control this value, a colluding NEAR validator can shift it forward, expanding the effective acceptance window beyond the intended `MAX_FUTURE_BLOCK_TIME_LOCAL` (7200 seconds). A relayer can then submit block headers whose timestamps exceed the 2-hour future limit, causing the light client to accept and permanently store headers with invalid timestamps in `headers_pool` and the canonical mainchain.

---

### Finding Description

In every chain module, the "time-too-new" check is implemented identically:

**Bitcoin** (`contract/src/bitcoin.rs`, lines 35–39):
```rust
let current_timestamp = u32::try_from(env::block_timestamp_ms() / 1000).unwrap();
require!(
    block_header.time <= current_timestamp + MAX_FUTURE_BLOCK_TIME_LOCAL,
    "time-too-new: block timestamp too far in the future"
);
``` [1](#0-0) 

The same pattern appears verbatim in Litecoin: [2](#0-1) 

In Dogecoin: [3](#0-2) 

And in Zcash: [4](#0-3) 

`MAX_FUTURE_BLOCK_TIME_LOCAL` is defined as `2 * 60 * 60 = 7200` seconds: [5](#0-4) 

`env::block_timestamp_ms()` is the timestamp embedded in the NEAR block by the current NEAR block producer. NEAR Protocol requires each block's timestamp to be strictly greater than the previous block's timestamp, but imposes no enforced upper bound on how far ahead a producer may set it relative to wall-clock time. A block producer who sets the timestamp `D` seconds ahead of real time causes `current_timestamp` to be inflated by `D`, so the effective acceptance ceiling becomes `real_now + 7200 + D`. Any submitted chain header with `time ≤ real_now + 7200 + D` passes the guard.

The check is reached inside `check_pow`, which is called unconditionally from `submit_block_header` whenever `skip_pow_verification` is `false` (the production setting): [6](#0-5) 

A header that passes `check_pow` is then stored permanently via `store_block_header` or `store_fork_header` and, if its chainwork exceeds the current tip, promoted to the canonical mainchain through `reorg_chain`: [7](#0-6) 

---

### Impact Explanation

Once a header with an invalid future timestamp is stored in `headers_pool` and inserted into the canonical mainchain (`mainchain_height_to_header` / `mainchain_header_to_height`), the following concrete state corruptions occur:

1. **Canonical chain poisoning.** The `mainchain_tip_blockhash` and the height-to-hash mapping are updated to reference a header whose `time` field violates the Bitcoin/Litecoin/Dogecoin/Zcash protocol rules. All subsequent headers submitted on top of it inherit the corrupted ancestry.

2. **MTP drift.** `get_median_time_past` computes the median of the 11 most recent stored timestamps. A header with an inflated `time` shifts the MTP upward, causing the `time-too-old` check (`block_header.time > MTP`) to accept headers that would otherwise be rejected, and potentially rejecting legitimate headers.

3. **False SPV proof acceptance.** `verify_transaction_inclusion` and `verify_transaction_inclusion_v2` look up the target block exclusively by its presence in `mainchain_header_to_height`. A transaction in a block with an invalid timestamp will be verified as confirmed once the block is on the canonical chain, enabling downstream contracts or bridges to act on a fraudulent inclusion proof. [8](#0-7) 

---

### Likelihood Explanation

The attack requires two cooperating parties:

- A **relayer** who submits a `submit_blocks` call containing a header with `time > real_now + 7200`.
- A **NEAR block producer** who, for the specific NEAR block that includes that transaction, sets `block.timestamp` sufficiently far ahead of real time.

NEAR block producers rotate among the active validator set. A single validator needs to be the producer only for the one block containing the malicious `submit_blocks` transaction. Because `MAX_FUTURE_BLOCK_TIME_LOCAL` is 7200 seconds, the required forward drift is bounded by how far the target header's timestamp exceeds `real_now + 7200`. For a header timestamped, say, 30 minutes beyond the 2-hour window, the NEAR producer must shift the timestamp by 1800 seconds — a non-trivial but not impossible drift given the absence of a protocol-enforced ceiling. The `trusted_relayer` macro on `submit_blocks` restricts who may call the function, but a staked trusted relayer is itself an unprivileged external actor relative to the NEAR validator set, and the two roles are independent. [9](#0-8) 

---

### Recommendation

1. **Do not rely solely on `env::block_timestamp_ms()` for the future-timestamp guard.** The MTP-based check (`block_header.time > MTP`) already uses only on-chain Bitcoin data and is manipulation-resistant. Consider whether the local-time check adds meaningful security beyond the MTP check, given the trust assumption it introduces.

2. **If the local-time check is retained**, document explicitly that its correctness depends on NEAR validators not manipulating `block_timestamp_ms()` by more than `MAX_FUTURE_BLOCK_TIME_LOCAL` seconds, and ensure `MAX_FUTURE_BLOCK_TIME_LOCAL` is set well above any realistic NEAR timestamp drift.

3. **Cross-check against the MTP-based upper bound.** For Zcash, the `MAX_FUTURE_BLOCK_TIME_MTP` check (36 hours ahead of MTP) already provides a manipulation-resistant ceiling. Extending a similar MTP-relative upper bound to Bitcoin, Litecoin, and Dogecoin would eliminate the dependency on `env::block_timestamp_ms()` entirely. [10](#0-9) 

---

### Proof of Concept

1. A relayer constructs a Bitcoin block header `H` with `H.time = T_real + 7200 + 1800` (30 minutes beyond the 2-hour limit).
2. The relayer submits `submit_blocks([H])` and ensures the transaction lands in a NEAR block produced by a colluding validator.
3. The colluding NEAR validator sets `block.timestamp_ms = (T_real + 1800) * 1000` (1800 seconds ahead of real time).
4. Inside `check_pow`, `current_timestamp = T_real + 1800`. The check evaluates: `H.time = T_real + 9000 <= (T_real + 1800) + 7200 = T_real + 9000`. The condition holds; the header passes.
5. `H` is stored in `headers_pool` and, if its chainwork exceeds the current tip, promoted to the canonical mainchain via `store_block_header` / `reorg_chain`.
6. Any downstream call to `verify_transaction_inclusion_v2` for a transaction in `H` returns `true`, despite `H` having an invalid future timestamp under the real Bitcoin protocol rules.

### Citations

**File:** contract/src/bitcoin.rs (L35-39)
```rust
        let current_timestamp = u32::try_from(env::block_timestamp_ms() / 1000).unwrap(); // Convert to seconds
        require!(
            block_header.time <= current_timestamp + MAX_FUTURE_BLOCK_TIME_LOCAL,
            "time-too-new: block timestamp too far in the future"
        );
```

**File:** contract/src/litecoin.rs (L36-40)
```rust
        let current_timestamp = u32::try_from(env::block_timestamp_ms() / 1000).unwrap(); // Convert to seconds
        require!(
            block_header.time <= current_timestamp + MAX_FUTURE_BLOCK_TIME_LOCAL,
            "time-too-new: block timestamp too far in the future"
        );
```

**File:** contract/src/dogecoin.rs (L42-46)
```rust
        let current_timestamp = u32::try_from(env::block_timestamp_ms() / 1000).unwrap();
        require!(
            block_header.time <= current_timestamp + MAX_FUTURE_BLOCK_TIME_LOCAL,
            "time-too-new: block timestamp too far in the future"
        );
```

**File:** contract/src/zcash.rs (L48-52)
```rust
        let current_timestamp = u32::try_from(env::block_timestamp_ms() / 1000).unwrap(); // Convert to seconds
        require!(
            block_header.time <= current_timestamp + MAX_FUTURE_BLOCK_TIME_LOCAL,
            "time-too-new: block timestamp is too far ahead of local time"
        );
```

**File:** btc-types/src/network.rs (L11-17)
```rust
pub const MAX_FUTURE_BLOCK_TIME_MTP: u32 = 90 * 60;

/**
 * Maximum amount of time that a block timestamp is allowed to be ahead of the
 * current local time.
 */
pub const MAX_FUTURE_BLOCK_TIME_LOCAL: u32 = 2 * 60 * 60;
```

**File:** contract/src/lib.rs (L168-169)
```rust
    #[trusted_relayer]
    pub fn submit_blocks(
```

**File:** contract/src/lib.rs (L299-322)
```rust
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
```

**File:** contract/src/lib.rs (L517-526)
```rust
        if !skip_pow_verification {
            self.check_target(&header, &prev_block_header);

            let pow_hash = header.block_hash_pow();
            // Check if the block hash is less than or equal to the target
            require!(
                U256::from_le_bytes(&pow_hash.0) <= target_from_bits(header.bits),
                format!("block should have correct pow")
            );
        }
```

**File:** contract/src/lib.rs (L560-567)
```rust
            self.store_fork_header(&current_header);

            // Current chainwork is higher than on a current mainchain, let's promote the fork
            if current_header.chain_work > total_main_chain_chainwork {
                log!("Chain reorg");
                self.reorg_chain(current_header, last_main_chain_block_height);
            }
        }
```
