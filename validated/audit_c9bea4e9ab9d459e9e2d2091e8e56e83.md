### Title
Unchecked Boolean Return Value of `equihash::is_valid_solution` Allows Invalid PoW Blocks to Be Accepted — (`contract/src/zcash.rs`)

### Summary

In the Zcash build of the BTC light client, `check_pow` calls `equihash::is_valid_solution`, which returns `Result<bool, Error>`. The code handles the `Err` variant by panicking, but silently discards the inner `bool`. When the function returns `Ok(false)` — meaning the Equihash solution is well-formed but cryptographically invalid — the contract does not reject the block. The header is stored into the canonical chain as if PoW was satisfied.

### Finding Description

In `contract/src/zcash.rs`, the Equihash validation reads:

```rust
equihash::is_valid_solution(n, k, &input, &block_header.nonce.0, &block_header.solution)
    .unwrap_or_else(|e| {
        env::panic_str(&format!("Invalid Equihash solution: {e}"));
    });
```

`equihash::is_valid_solution` has the signature:

```rust
pub fn is_valid_solution(n: u32, k: u32, input: &[u8], nonce: &[u8], solution: &[u8]) -> Result<bool, Error>
```

- `Ok(true)` — solution satisfies the Equihash constraints (valid PoW)
- `Ok(false)` — solution is well-formed (correct length, correct parameters) but does **not** satisfy the Equihash constraints (invalid PoW)
- `Err(e)` — malformed input (wrong solution length, unsupported parameters, etc.)

`.unwrap_or_else(|e| env::panic_str(...))` only handles the `Err` branch. The `Ok(bool)` value is returned from `unwrap_or_else` and immediately dropped. The `Ok(false)` case — the exact case that represents a cryptographically invalid solution — is never checked. Execution continues past the validation call, and `submit_block_header_inner` stores the header. [1](#0-0) 

The analogous pattern in the external report is:

```solidity
badger.transfer(msg.sender, badgerBalanceDiff); // return value not checked
return (badgerBalanceDiff);                      // state updated as if transfer succeeded
```

Here the pattern is:

```rust
equihash::is_valid_solution(...).unwrap_or_else(|e| env::panic_str(...)); // bool not checked
// execution continues → header stored into canonical chain
```

### Impact Explanation

An attacker can submit a Zcash block header whose `solution` field is correctly sized (so `Err` is not triggered) but does not satisfy the Equihash proof-of-work puzzle. The contract accepts the header, stores it in `headers_pool`, and potentially promotes it to the canonical chain tip via `submit_block_header_inner`. Once on the canonical chain:

- `verify_transaction_inclusion` and `verify_transaction_inclusion_v2` will return `true` for transactions claimed to be in that invalid block, because both functions look up the block only by its presence in `mainchain_header_to_height`.
- Any downstream consumer contract relying on the light client's SPV proofs will treat fabricated transaction inclusions as verified.
- `mainchain_tip_blockhash`, `mainchain_height_to_header`, and `mainchain_header_to_height` are all corrupted with a block that never had valid PoW. [2](#0-1) [3](#0-2) 

### Likelihood Explanation

The `submit_blocks` entrypoint is callable by any account that satisfies the `trusted_relayer` check. The `trusted_relayer` macro allows bypass via `Role::UnrestrictedSubmitBlocks`, and the relayer staking mechanism is managed separately. More importantly, the Zcash feature build is a production target. Constructing a well-formed but invalid Equihash solution (correct byte length for n=200, k=9) requires no special privilege — only knowledge of the expected solution size. The attacker does not need to solve the puzzle; they only need to avoid triggering the `Err` path (wrong length/parameters). [4](#0-3) 

### Recommendation

Check the `bool` return value explicitly:

```rust
let valid = equihash::is_valid_solution(n, k, &input, &block_header.nonce.0, &block_header.solution)
    .unwrap_or_else(|e| {
        env::panic_str(&format!("Invalid Equihash solution: {e}"));
    });
require!(valid, "Invalid Equihash solution: proof-of-work check failed");
```

Or equivalently, use `require!` directly on the result:

```rust
require!(
    equihash::is_valid_solution(n, k, &input, &block_header.nonce.0, &block_header.solution)
        .unwrap_or_else(|e| env::panic_str(&format!("Invalid Equihash solution: {e}"))),
    "Invalid Equihash solution"
);
``` [5](#0-4) 

### Proof of Concept

1. Build the contract with `--features zcash`.
2. Initialize the contract with a valid Zcash genesis block.
3. Construct a `ZcashHeader` whose `solution` field has the correct byte length for n=200, k=9 (1344 bytes) but contains arbitrary bytes that do not satisfy the Equihash constraints.
4. Call `submit_blocks` with this header.
5. Observe that the call succeeds (no panic), and `get_last_block_header` returns the invalid header as the new chain tip.
6. Call `verify_transaction_inclusion` with a fabricated Merkle proof against a transaction hash of your choice, using the invalid block's hash. The function returns `true` if the Merkle proof is constructed to match the `merkle_root` field you placed in the invalid header.

The root cause is at: [6](#0-5)

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

**File:** contract/src/lib.rs (L166-198)
```rust
    #[payable]
    #[pause]
    #[trusted_relayer]
    pub fn submit_blocks(
        &mut self,
        #[serializer(borsh)] headers: Vec<BlockHeader>,
    ) -> PromiseOrValue<()> {
        let amount = env::attached_deposit();
        let initial_storage = env::storage_usage();
        let num_of_headers = headers.len().try_into().unwrap();

        for header in headers {
            self.submit_block_header(header, self.skip_pow_verification);
        }

        self.run_mainchain_gc(num_of_headers);
        let diff_storage_usage = env::storage_usage().saturating_sub(initial_storage);
        let required_deposit = env::storage_byte_cost().saturating_mul(diff_storage_usage.into());

        require!(
            amount >= required_deposit,
            format!("Required deposit {}", required_deposit)
        );

        let refund = amount.saturating_sub(required_deposit);
        if refund > NearToken::from_near(0) {
            Promise::new(env::predecessor_account_id())
                .transfer(refund)
                .into()
        } else {
            PromiseOrValue::Value(())
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

**File:** contract/src/lib.rs (L531-568)
```rust
    fn submit_block_header_inner(
        &mut self,
        current_header: ExtendedHeader,
        prev_block_header: &ExtendedHeader,
    ) {
        // Main chain submission
        if prev_block_header.block_hash == self.mainchain_tip_blockhash {
            // Probably we should check if it is not in a mainchain?
            // chainwork > highScore
            log!("Block {}: saving to mainchain", current_header.block_hash);
            // Validate chain
            assert_eq!(
                self.mainchain_tip_blockhash,
                current_header.block_header.prev_block_hash
            );

            self.store_block_header(&current_header);
            self.mainchain_tip_blockhash = current_header.block_hash;
        } else {
            log!("Block {}: saving to fork", current_header.block_hash);
            // Fork submission
            let main_chain_tip_header = self
                .headers_pool
                .get(&self.mainchain_tip_blockhash)
                .unwrap_or_else(|| env::panic_str("tip should be in a header pool"));

            let last_main_chain_block_height = main_chain_tip_header.block_height;
            let total_main_chain_chainwork = main_chain_tip_header.chain_work;

            self.store_fork_header(&current_header);

            // Current chainwork is higher than on a current mainchain, let's promote the fork
            if current_header.chain_work > total_main_chain_chainwork {
                log!("Chain reorg");
                self.reorg_chain(current_header, last_main_chain_block_height);
            }
        }
    }
```
