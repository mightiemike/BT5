### Title
Missing Update Function for `skip_pow_verification` Config Field Permanently Locks PoW Bypass State - (File: contract/src/lib.rs)

### Summary
The `BtcLightClient` contract stores three mutable configuration fields — `skip_pow_verification`, `gc_threshold`, and `network` — that are set once at `init` and have no privileged update functions. The most security-critical of these is `skip_pow_verification`: if the contract is deployed with `skip_pow_verification = true` (a supported and documented deployment path), the DAO has no on-chain function to revert it to `false` without executing a full contract upgrade cycle. During that window, every `submit_blocks` call skips all PoW and difficulty validation, allowing any trusted relayer to inject headers with arbitrary `bits` values, corrupting `chain_work` and the canonical chain tip.

### Finding Description

The `BtcLightClient` struct holds three init-time config fields:

```rust
skip_pow_verification: bool,
gc_threshold: u64,
network: Network,
``` [1](#0-0) 

All three are written once in `init` and never touched again by any public or role-gated function: [2](#0-1) 

The contract exposes rich role-based governance (`PauseManager`, `RelayerManager`, `DAO`, `CodeStager`, `CodeDeployer`, `DurationManager`) and a full `Upgradable` pipeline, yet provides zero setter for any of these three fields.

`skip_pow_verification` is consumed directly in `submit_blocks`:

```rust
self.submit_block_header(header, self.skip_pow_verification);
``` [3](#0-2) 

When `skip_pow_verification = true`, `submit_block_header` skips both `check_target` (difficulty validation) and the PoW hash check entirely: [4](#0-3) 

The relayer's `InitConfig` exposes `skip_pow_verification` as a plain config field, and the integration test suite initialises the contract with `skip_pow_verification: true` as the standard test posture: [5](#0-4) 

The contract documentation itself warns: *"skip_pow_verification = false: Should be set to false for standard use. Set to true only for testing purposes."* [6](#0-5) 

Despite this warning, there is no function the DAO can call to enforce `false` after deployment.

### Impact Explanation

If the contract is deployed with `skip_pow_verification = true` — whether accidentally, for a staged rollout, or during a testnet-to-mainnet migration — the PoW bypass is permanent until a full upgrade cycle completes. During that window:

- Any account holding `Role::UnrestrictedSubmitBlocks` or `Role::DAO` (both bypass the `#[trusted_relayer]` guard) can submit block headers with arbitrary `bits` values.
- Headers with inflated `bits` (claiming enormous work) are accepted and their `chain_work` is accumulated into `chain_work` of `ExtendedHeader`, corrupting the canonical tip pointer `mainchain_tip_blockhash`.
- Corrupted `chain_work` triggers false reorgs via `reorg_chain`, replacing the legitimate mainchain with an attacker-controlled fork.
- `verify_transaction_inclusion` and `verify_transaction_inclusion_v2` then return results against the corrupted canonical chain, producing false SPV proof outcomes for any downstream consumer. [7](#0-6) 

### Likelihood Explanation

The relayer's `InitConfig` struct includes `skip_pow_verification` as a plain boolean field with no default: [8](#0-7) 

The example config and integration tests both set it to `true`. A deployer who forgets to redeploy with `false`, or who intentionally uses `true` for a phased launch, has no recovery path short of a full upgrade. The `Upgradable` pipeline (stage → deploy → migrate) is the only workaround, and it requires the `CodeStager`/`CodeDeployer`/`DAO` roles to coordinate a new `migrate` function that patches the field — a non-trivial operational burden with its own risk surface.

### Recommendation

Add a DAO-gated setter for `skip_pow_verification` (and analogously for `gc_threshold`):

```rust
pub fn set_skip_pow_verification(&mut self, skip: bool) {
    near_sdk::require!(
        self.acl_has_role(&Role::DAO, &env::predecessor_account_id()),
        "Requires DAO role"
    );
    self.skip_pow_verification = skip;
}

pub fn set_gc_threshold(&mut self, threshold: u64) {
    near_sdk::require!(
        self.acl_has_role(&Role::DAO, &env::predecessor_account_id()),
        "Requires DAO role"
    );
    require!(threshold > 0, "gc_threshold must be positive");
    self.gc_threshold = threshold;
}
```

This mirrors the pattern already used for pause/unpause and relayer management, and eliminates the need for a full upgrade cycle to correct a misconfigured init parameter.

### Proof of Concept

1. Deploy the contract with `skip_pow_verification: true` (as in the standard test setup at `contract/tests/test_basics.rs:71`).
2. Attempt to call any function to set `skip_pow_verification = false` — no such function exists in the public or role-gated API.
3. Grant `Role::UnrestrictedSubmitBlocks` to an account (the contract itself is super-admin and can do this).
4. Submit a block header with `bits = 0x1d00ffff` (maximum work per block) — accepted without PoW hash check.
5. Observe `chain_work` inflated beyond the legitimate tip; a subsequent fork submission triggers `reorg_chain`, replacing the canonical chain.
6. Call `verify_transaction_inclusion` against a transaction in the attacker-controlled fork — returns `true` for a fabricated inclusion. [9](#0-8)

### Citations

**File:** contract/src/lib.rs (L110-117)
```rust
    // If we should run all the block checks or not
    skip_pow_verification: bool,

    // GC threshold - how many blocks we would like to store in memory, and GC the older ones
    gc_threshold: u64,

    // Network type Mainnet/Testnet
    network: Network,
```

**File:** contract/src/lib.rs (L130-131)
```rust
    /// * `skip_pow_verification = false`: Should be set to `false` for standard use. Set to `true` only for testing purposes.
    /// * `gc_threshold = 52704`: This is the approximate number of blocks generated in a year.
```

**File:** contract/src/lib.rs (L142-144)
```rust
            skip_pow_verification: args.skip_pow_verification,
            gc_threshold: args.gc_threshold,
            network: args.network,
```

**File:** contract/src/lib.rs (L177-179)
```rust
        for header in headers {
            self.submit_block_header(header, self.skip_pow_verification);
        }
```

**File:** contract/src/lib.rs (L488-529)
```rust
    #[cfg(not(feature = "dogecoin"))]
    #[allow(clippy::needless_pass_by_value)]
    fn submit_block_header(&mut self, header: Header, skip_pow_verification: bool) {
        // We do not have a previous block in the headers_pool, there is a high probability
        // it means we are starting to receive a new fork,
        // so what we do now is we are returning the error code
        // to ask the relay to deploy the previous block.
        //
        // Offchain relay now, should submit blocks one by one in decreasing height order
        // 80 -> 79 -> 78 -> ...
        // And do it until we can accept the block.
        // It means we found an initial fork position.
        // We are starting to gather new fork from this initial position.
        #[allow(clippy::useless_conversion)]
        let prev_block_header = self.get_prev_header(&header.clone().into());
        let current_block_hash = header.block_hash();

        let (current_block_computed_chain_work, overflow) = prev_block_header
            .chain_work
            .overflowing_add(work_from_bits(header.bits));
        require!(!overflow, "Addition of U256 values overflowed");

        let current_header = ExtendedHeader {
            block_header: header.clone().into_light(),
            block_hash: current_block_hash,
            chain_work: current_block_computed_chain_work,
            block_height: 1 + prev_block_header.block_height,
        };

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
    }
```

**File:** contract/src/lib.rs (L562-566)
```rust
            // Current chainwork is higher than on a current mainchain, let's promote the fork
            if current_header.chain_work > total_main_chain_chainwork {
                log!("Chain reorg");
                self.reorg_chain(current_header, last_main_chain_block_height);
            }
```

**File:** contract/tests/test_basics.rs (L68-75)
```rust
        let args = InitArgs {
            genesis_block_hash: submit_blocks[0].block_hash(),
            genesis_block_height: 0,
            skip_pow_verification: true,
            gc_threshold: 20,
            network: btc_types::network::Network::Mainnet,
            submit_blocks,
        };
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
