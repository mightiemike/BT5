### Title
Equihash Solution Validity Result Silently Discarded, Allowing Invalid PoW Blocks to Be Accepted - (`contract/src/zcash.rs`)

### Summary

In the Zcash build of the BTC light client, the `check_pow` function calls `equihash::is_valid_solution(...)` but discards the returned `bool` validity result. Only the `Err` variant is handled (via `.unwrap_or_else`). An `Ok(false)` return — meaning the solution was checked and found **invalid** — is silently dropped, allowing a block with a forged Equihash solution to pass PoW verification and be accepted into the canonical chain.

### Finding Description

In `contract/src/zcash.rs`, the Equihash solution check is written as:

```rust
equihash::is_valid_solution(n, k, &input, &block_header.nonce.0, &block_header.solution)
    .unwrap_or_else(|e| {
        env::panic_str(&format!("Invalid Equihash solution: {e}"));
    });
``` [1](#0-0) 

The `equihash::is_valid_solution` function returns `Result<bool, Error>`:
- `Err(e)` → the `.unwrap_or_else` closure panics. ✓
- `Ok(true)` → solution is valid, the `bool` is dropped. ✓
- `Ok(false)` → solution is **invalid**, but the `bool` is silently dropped and execution continues. ✗

The returned `bool` is never bound to a variable or passed to `require!`. The code structurally mirrors the original report's `if (!isContract(target)) Errors.AddressNotContract;` — the check is invoked, the result is computed, but the enforcement step is missing.

This is the only PoW validity gate for Zcash blocks. The `check_pow` function is called from `submit_block_header` (the non-dogecoin path) when `skip_pow_verification == false`:

```rust
if !skip_pow_verification {
    self.check_target(&header, &prev_block_header);
    let pow_hash = header.block_hash_pow();
    require!(
        U256::from_le_bytes(&pow_hash.0) <= target_from_bits(header.bits),
        ...
    );
}
``` [2](#0-1) 

For the Zcash feature, `check_target` delegates to `check_pow` in `zcash.rs`, which contains the broken Equihash check. The hash-vs-target check that follows in `lib.rs` is irrelevant for Zcash because Equihash uses a different PoW scheme — the Equihash solution check **is** the PoW gate. [3](#0-2) 

### Impact Explanation

A malicious relayer can craft a Zcash block header with an arbitrary (invalid) Equihash solution. The `is_valid_solution` call returns `Ok(false)`, which is discarded. The block passes `check_pow` and is stored in `headers_pool` and promoted to the canonical chain via `submit_block_header_inner`. This corrupts `mainchain_tip_blockhash`, `mainchain_height_to_header`, and `mainchain_header_to_height`. Downstream callers of `verify_transaction_inclusion` or `verify_transaction_inclusion_v2` will receive proofs anchored to a fraudulent chain state.

### Likelihood Explanation

The `submit_blocks` entry point is gated by `#[trusted_relayer]`, meaning the caller must be a registered relayer or hold a bypass role (`Role::DAO` or `Role::UnrestrictedSubmitBlocks`). [4](#0-3) 

A malicious or compromised registered relayer is the realistic attacker. The `trusted_relayer` system does not prevent a registered participant from submitting adversarial data — it only restricts who can call the function. Once inside, the broken Equihash check provides no protection.

### Recommendation

Bind the result and assert it is `true`:

```rust
let is_valid = equihash::is_valid_solution(n, k, &input, &block_header.nonce.0, &block_header.solution)
    .unwrap_or_else(|e| {
        env::panic_str(&format!("Invalid Equihash solution: {e}"));
    });
require!(is_valid, "Invalid Equihash solution");
```

### Proof of Concept

1. Deploy the contract with the `zcash` feature and `skip_pow_verification = false`.
2. Register as a trusted relayer.
3. Construct a `Header` with a valid `bits` field (matching expected difficulty) but a completely zeroed or random `solution` field.
4. Call `submit_blocks` with this header.
5. `check_pow` calls `equihash::is_valid_solution(...)`, which returns `Ok(false)`.
6. `.unwrap_or_else` does not fire (no `Err`), the `false` is dropped, `check_pow` returns normally.
7. The block is stored and the canonical tip is updated to the fraudulent header.
8. `get_last_block_header()` now returns the attacker-controlled header; any subsequent `verify_transaction_inclusion` call against this height will use the forged `merkle_root`.

### Citations

**File:** contract/src/zcash.rs (L59-68)
```rust
        // Check Equihash solution
        let n = 200;
        let k = 9;
        let input = block_header.get_block_header_vec_for_equihash();

        equihash::is_valid_solution(n, k, &input, &block_header.nonce.0, &block_header.solution)
            .unwrap_or_else(|e| {
                env::panic_str(&format!("Invalid Equihash solution: {e}"));
            });
    }
```

**File:** contract/src/lib.rs (L166-172)
```rust
    #[payable]
    #[pause]
    #[trusted_relayer]
    pub fn submit_blocks(
        &mut self,
        #[serializer(borsh)] headers: Vec<BlockHeader>,
    ) -> PromiseOrValue<()> {
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
