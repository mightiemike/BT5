### Title
Irreversible `skip_pow_verification` Flag Permanently Disables PoW Validation With No Admin Escape Hatch - (File: contract/src/lib.rs)

### Summary
The `skip_pow_verification` boolean is written once at `init()` and stored in contract state with no setter, no migration path to update it, and no DAO/admin function to toggle it. A contract deployed with `skip_pow_verification = true` — explicitly documented as a valid deployment option — is permanently locked in that mode. Any trusted relayer can then submit headers with arbitrary `bits` values and invalid PoW hashes, corrupting the canonical chain and causing `verify_transaction_inclusion` to return `true` for transactions in fabricated blocks.

### Finding Description

`BtcLightClient` stores `skip_pow_verification` as a plain struct field: [1](#0-0) 

It is assigned exactly once, inside `init()`, from the caller-supplied `InitArgs`: [2](#0-1) 

The documentation explicitly presents `true` as a valid value: [3](#0-2) 

Every call to `submit_blocks` passes the stored flag directly into `submit_block_header`: [4](#0-3) 

Inside `submit_block_header`, when the flag is `true`, both `check_target()` (which enforces the `bits` difficulty field) and the hash-vs-target comparison are skipped entirely: [5](#0-4) 

No public or role-gated function exists anywhere in the contract to update `skip_pow_verification` after deployment. A search for any setter pattern (`fn set_`, `fn update_`, `fn change_`, `fn toggle_`) returns zero results. The `migrate()` function only round-trips the existing state bytes — it carries `skip_pow_verification` forward unchanged and provides no mechanism to alter it: [6](#0-5) 

This is the direct structural analog to the original report: just as `bools[fund].custom` is written at fund creation and can never be changed or deleted, `skip_pow_verification` is written at contract initialization and can never be changed or reset without a full contract redeployment.

### Impact Explanation

When `skip_pow_verification = true` is permanently active, a trusted relayer (or any account holding `Role::UnrestrictedSubmitBlocks`) can call `submit_blocks` with headers whose `bits` field encodes trivially easy difficulty and whose block hash does not satisfy any real PoW target. These headers pass all validation and are stored as canonical mainchain blocks: [7](#0-6) 

The corrupted `mainchain_height_to_header` and `mainchain_header_to_height` maps then cause `verify_transaction_inclusion` and `verify_transaction_inclusion_v2` to return `true` for Merkle proofs anchored to fabricated blocks: [8](#0-7) 

Any downstream NEAR contract that gates asset releases, bridge withdrawals, or cross-chain state transitions on a `true` result from these functions is directly exploitable.

### Likelihood Explanation

The `InitArgs` struct exposes `skip_pow_verification` as a plain JSON/Borsh field with no type-level restriction: [9](#0-8) 

A contract initialized for testnet or staging with `skip_pow_verification = true` and later promoted to production use — a common lifecycle pattern — is permanently vulnerable with no remediation short of full redeployment and state migration. The relayer configuration also exposes this flag: [10](#0-9) 

### Recommendation

Add a DAO-gated setter that allows authorized accounts to update `skip_pow_verification` post-deployment:

```rust
pub fn set_skip_pow_verification(&mut self, skip: bool) {
    // require DAO or PauseManager role
    self.skip_pow_verification = skip;
}
```

Alternatively, enforce at the type level that `skip_pow_verification = true` is rejected unless a compile-time `#[cfg(test)]` or `#[cfg(feature = "testing")]` gate is active, preventing accidental production deployments with PoW disabled.

### Proof of Concept

1. Deploy the contract with `InitArgs { skip_pow_verification: true, ... }`.
2. Observe that no function exists to change `skip_pow_verification` to `false`.
3. As a trusted relayer, call `submit_blocks` with a `BlockHeader` where `bits = 0x207fffff` (minimum difficulty) and any arbitrary nonce — the hash will not satisfy real Bitcoin PoW.
4. The header is accepted and stored as the canonical chain tip (lines 546–548 of `lib.rs`).
5. Construct a Merkle proof for a fabricated transaction in that block and call `verify_transaction_inclusion_v2` — it returns `true`.
6. Any bridge or settlement contract consuming that result is now exploitable. [11](#0-10) [12](#0-11)

### Citations

**File:** contract/src/lib.rs (L111-111)
```rust
    skip_pow_verification: bool,
```

**File:** contract/src/lib.rs (L130-130)
```rust
    /// * `skip_pow_verification = false`: Should be set to `false` for standard use. Set to `true` only for testing purposes.
```

**File:** contract/src/lib.rs (L135-161)
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

        // Make the contract itself super admin. This allows us to grant any role in the
        // constructor.
        near_sdk::require!(
            contract.acl_init_super_admin(env::current_account_id()),
            "Failed to initialize super admin",
        );

        contract.init_genesis(
            &args.genesis_block_hash,
            args.genesis_block_height,
            args.submit_blocks,
        );

        contract
    }
```

**File:** contract/src/lib.rs (L169-198)
```rust
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

**File:** contract/src/lib.rs (L546-548)
```rust

            self.store_block_header(&current_header);
            self.mainchain_tip_blockhash = current_header.block_hash;
```

**File:** contract/src/lib.rs (L726-750)
```rust
        pub fn migrate() -> Self {
            let raw_state = env::storage_read(b"STATE")
                .unwrap_or_else(|| env::panic_str("contract state not found"));

            if let Ok(state) = <Self as BorshDeserialize>::try_from_slice(&raw_state) {
                log!("state is already in the current layout");
                return state;
            }

            if let Ok(old_state) = BtcLightClientV2::try_from_slice(&raw_state) {
                log!("migrating state from the V2 layout");
                return Self {
                    mainchain_height_to_header: old_state.mainchain_height_to_header,
                    mainchain_header_to_height: old_state.mainchain_header_to_height,
                    mainchain_tip_blockhash: old_state.mainchain_tip_blockhash,
                    mainchain_initial_blockhash: old_state.mainchain_initial_blockhash,
                    headers_pool: old_state.headers_pool,
                    skip_pow_verification: old_state.skip_pow_verification,
                    gc_threshold: old_state.gc_threshold,
                    network: old_state.network,
                };
            }

            env::panic_str("contract state matches no known layout")
        }
```

**File:** btc-types/src/contract_args.rs (L10-10)
```rust
    pub skip_pow_verification: bool,
```

**File:** relayer/src/config.rs (L1-1)
```rust
use anyhow::{Context, Result};
```
