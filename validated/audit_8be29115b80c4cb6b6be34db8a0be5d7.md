### Title
Equihash Solution Validity Return Value Discarded — Block Headers Accepted Without Valid PoW - (`contract/src/zcash.rs`)

### Summary

`check_pow` in the Zcash build calls `equihash::is_valid_solution`, which returns `Result<bool, Error>`. The code handles only the `Err` variant (panicking on error) but silently discards the `bool` inside `Ok`. An `Ok(false)` result — meaning the solution is cryptographically invalid — causes no panic and no rejection. The block header is accepted as if PoW were satisfied.

### Finding Description

In `contract/src/zcash.rs`, the Equihash validation is:

```rust
equihash::is_valid_solution(n, k, &input, &block_header.nonce.0, &block_header.solution)
    .unwrap_or_else(|e| {
        env::panic_str(&format!("Invalid Equihash solution: {e}"));
    });
``` [1](#0-0) 

`equihash::is_valid_solution` (crate version 0.2.2) has the signature:

```rust
pub fn is_valid_solution(n: u32, k: u32, input: &[u8], nonce: &[u8], solution: &[u8]) -> Result<bool, Error>
```

The three possible outcomes are:
- `Ok(true)` — solution is valid
- `Ok(false)` — solution is **invalid** (wrong Equihash solution)
- `Err(e)` — malformed input (e.g., wrong solution length)

`.unwrap_or_else(|e| { env::panic_str(...) })` only intercepts `Err`. When the function returns `Ok(false)`, `.unwrap_or_else` unwraps to `false`, but that `bool` is the return value of the entire expression — which is immediately dropped because the statement has no binding. The `false` is never tested with `require!` or any conditional. Execution continues normally and the block is stored.

This is structurally identical to M-04: an external validation call returns a discriminating value (magic bytes / bool), but the code only guards against the error path and ignores the actual validity signal in the success path.

### Impact Explanation

An attacker can submit a Zcash block header that passes every other check (correct `bits`, valid timestamps, version ≥ 4) but carries a completely fabricated Equihash solution. `equihash::is_valid_solution` returns `Ok(false)`, the `false` is discarded, and the header is inserted into `headers_pool` and potentially promoted to the canonical mainchain tip via `submit_block_header_inner`.

Downstream callers of `verify_transaction_inclusion` or `verify_transaction_inclusion_v2` then operate against a canonical chain that contains headers with no real proof-of-work. A transaction inclusion proof built against such a header will return `true` for a transaction that was never confirmed on the real Zcash chain. [2](#0-1) [3](#0-2) 

### Likelihood Explanation

The `submit_blocks` entrypoint is reachable by any account that satisfies the `#[trusted_relayer]` staking requirement — a permissionless economic mechanism, not a fixed whitelist. [4](#0-3)  The attacker only needs to stake the required amount, then submit a header with a zeroed or arbitrary `solution` field. No privileged key or social engineering is required. The Zcash header type explicitly carries a `solution: Vec<u8>` field that is fully attacker-controlled. [5](#0-4) 

### Recommendation

Capture the `bool` result and assert it:

```rust
let valid = equihash::is_valid_solution(n, k, &input, &block_header.nonce.0, &block_header.solution)
    .unwrap_or_else(|e| {
        env::panic_str(&format!("Invalid Equihash solution: {e}"));
    });
require!(valid, "Invalid Equihash solution: solution does not satisfy Equihash(200,9)");
```

This mirrors the fix recommended in M-04: capture the return value and explicitly assert it equals the expected sentinel (`true` here, `IERC721Receiver.onERC721Received.selector` there).

### Proof of Concept

1. Build the contract with `feature = "zcash"`.
2. Initialize with a valid Zcash genesis block.
3. Construct a `Header` whose `version`, `prev_block_hash`, `bits`, and `time` are all valid, but whose `solution` is 1344 bytes of zeros (a structurally well-formed but cryptographically invalid Equihash solution — `equihash::is_valid_solution` returns `Ok(false)` for this input, not `Err`).
4. Call `submit_blocks` with this header.
5. Observe that the call succeeds and `get_last_block_header()` returns the forged header as the new chain tip.
6. Build a fake Merkle proof against this header's `merkle_root` and call `verify_transaction_inclusion_v2` — it returns `true` for a transaction that does not exist on the real Zcash chain. [6](#0-5)

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

**File:** contract/src/lib.rs (L120-124)
```rust
#[trusted_relayer(
    bypass_roles(Role::DAO, Role::UnrestrictedSubmitBlocks),
    manager_roles(Role::DAO, Role::RelayerManager),
    config_roles(Role::DAO)
)]
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

**File:** contract/src/lib.rs (L517-528)
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

        self.submit_block_header_inner(current_header, &prev_block_header);
```

**File:** btc-types/src/zcash_header.rs (L26-28)
```rust
    #[serde(deserialize_with = "hex::serde::deserialize")]
    #[serde(serialize_with = "hex::serde::serialize")]
    pub solution: Vec<u8>,
```
