### Title
Uninitialized Upgrade Staging Duration Allows Immediate Code Deployment Without Timelock - (File: `contract/src/lib.rs`)

### Summary
The `BtcLightClient` contract derives `Upgradable` from `near-plugins` and defines a `DurationManager` role intended to enforce a staging delay before new WASM code can be deployed. However, the `init()` function never calls `up_init_staging_duration`, leaving the staging duration permanently uninitialized (`None`). As a result, any account holding the `DAO` or `CodeDeployer` role can call `up_stage_code` and immediately follow with `up_deploy_code` — with zero enforced delay — replacing the on-chain WASM with arbitrary code.

### Finding Description

The contract applies the `Upgradable` derive macro and configures five role-gated upgrade methods: [1](#0-0) 

The `DurationManager` role is explicitly documented as the mechanism to initialize and update the staging duration — the timelock that separates `up_stage_code` from `up_deploy_code`: [2](#0-1) 

However, the `init()` function — the sole constructor — never calls `up_init_staging_duration`: [3](#0-2) 

No other location in `contract/src/` calls `up_init_staging_duration`. The `grep_search` across all of `contract/src/**` for `staging_duration|up_init` returns only two matches, both in `lib.rs`, and neither is a call to initialize the duration.

In `near-plugins` v0.4.1 (the pinned version), when the staging duration storage key is absent (`None`), `up_deploy_code` treats the elapsed-time check as satisfied and proceeds with deployment. This means the two-step upgrade process collapses into a single atomic sequence: stage then immediately deploy. [4](#0-3) 

### Impact Explanation

The BTC light client is an on-chain oracle. Downstream NEAR contracts call `verify_transaction_inclusion_v2` to confirm Bitcoin SPV proofs before releasing funds or updating state: [5](#0-4) 

A `DAO` or `CodeDeployer` role holder can deploy replacement WASM that makes `verify_transaction_inclusion_v2` unconditionally return `true`, or that corrupts `mainchain_tip_blockhash` / `mainchain_height_to_header` to forge canonical-chain membership. Every downstream contract that trusts the light client's verification result would accept fraudulent SPV proofs, enabling theft of bridged assets or double-spend acceptance.

### Likelihood Explanation

The `DAO` role is a super-privileged role granted to the contract account itself at initialization: [6](#0-5) 

Any account that subsequently receives `DAO` or `CodeDeployer` via `acl_grant_role` can exploit this immediately. Because no timelock is enforced, there is no window for downstream consumers or monitors to detect the staged code and exit before deployment. The absence of the initialization call is a deployment-time omission that is already present in the live contract.

### Recommendation

Call `up_init_staging_duration` inside `init()` with a meaningful delay (e.g., 7 days expressed in nanoseconds) before returning the constructed state. Additionally, grant the `DurationManager` role to a DAO-controlled account rather than leaving duration management ungated. This ensures that even a compromised `CodeDeployer` cannot bypass the timelock by resetting the duration to zero.

### Proof of Concept

1. Deploy the contract; `init()` completes without ever calling `up_init_staging_duration`. The `near-plugins` staging duration storage slot remains absent.
2. As an account holding `DAO` or `CodeDeployer`, call `up_stage_code(<malicious_wasm_bytes>)`.
3. Immediately (same block or next block) call `up_deploy_code()`. Because the staging duration is `None`, the elapsed-time guard in `near-plugins` v0.4.1 is not enforced and the call succeeds.
4. The contract WASM is now replaced. A subsequent call to `verify_transaction_inclusion_v2` executes the attacker's logic, returning `true` for any input.
5. Downstream bridge contracts accept the forged SPV proof and release funds.

### Citations

**File:** contract/src/lib.rs (L64-70)
```rust
    /// May successfully call `Upgradable` methods to initialize and update the staging duration
    /// since below it is passed to the attributes `duration_initializers`,
    /// `duration_update_stagers`, and `duration_update_appliers`.
    ///
    /// Using this pattern grantees of a single role are authorized to call multiple (but not all)
    /// protected `Upgradable` methods.
    DurationManager,
```

**File:** contract/src/lib.rs (L87-95)
```rust
#[derive(Pausable, Upgradable, PanicOnDefault)]
#[pausable(manager_roles(Role::PauseManager))]
#[upgradable(access_control_roles(
    code_stagers(Role::CodeStager, Role::DAO),
    code_deployers(Role::CodeDeployer, Role::DAO),
    duration_initializers(Role::DurationManager, Role::DAO),
    duration_update_stagers(Role::DurationManager, Role::DAO),
    duration_update_appliers(Role::DurationManager, Role::DAO),
))]
```

**File:** contract/src/lib.rs (L132-161)
```rust
    #[init]
    #[private]
    #[must_use]
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

**File:** contract/src/lib.rs (L347-369)
```rust
    pub fn verify_transaction_inclusion_v2(&self, #[serializer(borsh)] args: ProofArgsV2) -> bool {
        require!(
            args.merkle_proof.len() == args.coinbase_merkle_proof.len(),
            "Coinbase merkle proof and transaction merkle proof should have the same length"
        );

        let header = self
            .headers_pool
            .get(&args.tx_block_blockhash)
            .unwrap_or_else(|| env::panic_str("cannot find requested transaction block"));

        require!(
            merkle_tools::compute_root_from_merkle_proof(
                args.coinbase_tx_id.clone(),
                0usize,
                &args.coinbase_merkle_proof,
            ) == header.block_header.merkle_root,
            "Incorrect coinbase merkle proof"
        );

        #[allow(deprecated)]
        self.verify_transaction_inclusion(args.into())
    }
```

**File:** contract/Cargo.toml (L44-44)
```text
near-plugins = { git = "https://github.com/aurora-is-near/near-plugins", tag = "v0.4.1" }
```
