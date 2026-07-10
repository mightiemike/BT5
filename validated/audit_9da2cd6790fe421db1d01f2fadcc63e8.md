### Title
Missing Role Grants in `init()` Leaves All Privileged Functions Permanently Inaccessible - (File: contract/src/lib.rs)

### Summary
The `BtcLightClient::init()` function grants super-admin status only to the contract account itself (`env::current_account_id()`), but never grants any of the critical operational roles (`PauseManager`, `DAO`, `CodeStager`, `CodeDeployer`, `DurationManager`, `RelayerManager`) to any external human/deployer account. `InitArgs` accepts no admin address parameter. The relayer's deployment path (`relayer/src/main.rs::init_contract`) also performs no post-init role grants. As a result, every role-gated function in the contract is permanently inaccessible after deployment unless the deployer separately and manually calls `acl_grant_role` as the contract account — a step that is neither enforced by the contract nor present in the deployment flow.

### Finding Description
`BtcLightClient` uses `near-plugins` `AccessControllable`, `Pausable`, and `Upgradable` macros, all of which gate their critical methods behind specific roles:

- `PauseManager` → required to pause/unpause the contract (via `#[pausable(manager_roles(Role::PauseManager))]`)
- `DAO`, `CodeStager`, `CodeDeployer`, `DurationManager` → required for all `Upgradable` methods (`up_stage_code`, `up_deploy_code`, staging duration management)
- `DAO`, `RelayerManager` → required to manage trusted relayers (add/remove/configure)

The `init()` function only does:

```rust
contract.acl_init_super_admin(env::current_account_id());
```

This makes the **contract account** the super admin, not any human operator. No role is granted to any external account. The comment in the code even says *"This allows us to grant any role in the constructor"* — but then no roles are actually granted.

`InitArgs` has no `admin` or `dao` field:

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

The relayer's `init_contract()` function calls `near_client.init_contract(&args)` and returns — it performs no `acl_grant_role` calls after initialization.

The `migrate()` function is also `#[private]` and `#[init(ignore_state)]` and grants no roles either.

### Impact Explanation
**High.** Several protocol-critical functions can never be called after deployment:

1. **Contract cannot be paused.** No account holds `PauseManager`. If a critical bug is found in `submit_blocks` or `verify_transaction_inclusion`, there is no emergency stop. An attacker can continue exploiting the vulnerability indefinitely.
2. **Contract cannot be upgraded.** No account holds `DAO`, `CodeStager`, `CodeDeployer`, or `DurationManager`. The `Upgradable` interface is entirely blocked. Security patches cannot be deployed.
3. **Trusted relayer management is blocked.** No account holds `RelayerManager` or `DAO`. Malicious or compromised relayers cannot be removed. New legitimate relayers cannot be added through the managed path.

### Likelihood Explanation
**High.** The `init()` function accepts no admin parameter and grants no roles. The relayer deployment path (`relayer/src/main.rs::init_contract`) constructs `InitArgs` and calls `init` without any subsequent role-granting step. There is no on-chain enforcement requiring roles to be granted post-deployment. The deployer must manually call `acl_grant_role` as the contract account using a full access key — a step that is entirely absent from the documented and implemented deployment flow.

### Recommendation
Add an `admin` (or `dao`) field to `InitArgs` and grant the critical roles during `init()`, analogous to how `Infrared.sol` calls `_grantRole(GOVERNANCE_ROLE, _admin)`:

```rust
// In InitArgs
pub admin: near_sdk::AccountId,

// In init()
contract.acl_grant_role("PauseManager".to_string(), args.admin.clone());
contract.acl_grant_role("DAO".to_string(), args.admin.clone());
contract.acl_grant_role("RelayerManager".to_string(), args.admin.clone());
```

### Proof of Concept

1. Deploy the contract by calling `init` with any valid `InitArgs` (no admin field exists).
2. Attempt to call `pa_pause_feature` (pause) from any account — it will fail because no account holds `PauseManager`.
3. Attempt to call `up_stage_code` (upgrade staging) from any account — it will fail because no account holds `DAO`, `CodeStager`, or `DurationManager`.
4. Attempt to call any trusted relayer management method from any account — it will fail because no account holds `RelayerManager` or `DAO`.
5. Confirm: the only way to recover is if the deployer still holds a full access key on the contract account and manually calls `acl_grant_role` — a step absent from the relayer's `init_contract` deployment path.

**Root cause lines:** [1](#0-0) 

`InitArgs` has no admin field: [2](#0-1) 

Relayer deployment path grants no roles: [3](#0-2) 

Role definitions that are never granted during init: [4](#0-3) 

Upgradable role gates that are permanently blocked: [5](#0-4)

### Citations

**File:** contract/src/lib.rs (L40-73)
```rust
pub enum Role {
    /// May pause and unpause features.
    PauseManager,
    /// Allows to use contract API even after contract is paused
    UnrestrictedSubmitBlocks,
    // Allows to use `run_mainchain_gc` API on a paused contract
    UnrestrictedRunGC,
    /// May successfully call any of the protected `Upgradable` methods since below it is passed to
    /// every attribute of `access_control_roles`.
    ///
    /// Using this pattern grantees of a single role are authorized to call all `Upgradable`methods.
    DAO,
    /// May successfully call `Upgradable::up_stage_code`, but none of the other protected methods,
    /// since below is passed only to the `code_stagers` attribute.
    ///
    /// Using this pattern grantees of a role are authorized to call only one particular protected
    /// `Upgradable` method.
    CodeStager,
    /// May successfully call `Upgradable::up_deploy_code`, but none of the other protected methods,
    /// since below is passed only to the `code_deployers` attribute.
    ///
    /// Using this pattern grantees of a role are authorized to call only one particular protected
    /// `Upgradable` method.
    CodeDeployer,
    /// May successfully call `Upgradable` methods to initialize and update the staging duration
    /// since below it is passed to the attributes `duration_initializers`,
    /// `duration_update_stagers`, and `duration_update_appliers`.
    ///
    /// Using this pattern grantees of a single role are authorized to call multiple (but not all)
    /// protected `Upgradable` methods.
    DurationManager,
    /// May manage trusted relayer staking: reject applications and update relayer config.
    RelayerManager,
}
```

**File:** contract/src/lib.rs (L89-95)
```rust
#[upgradable(access_control_roles(
    code_stagers(Role::CodeStager, Role::DAO),
    code_deployers(Role::CodeDeployer, Role::DAO),
    duration_initializers(Role::DurationManager, Role::DAO),
    duration_update_stagers(Role::DurationManager, Role::DAO),
    duration_update_appliers(Role::DurationManager, Role::DAO),
))]
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

**File:** relayer/src/main.rs (L338-391)
```rust
async fn init_contract(
    bitcoin_client: &BitcoinClient,
    near_client: &NearClient,
    init_config: InitConfig,
) {
    info!("Init contract");

    let header_hash = bitcoin_client
        .get_block_hash(init_config.init_height)
        .expect("Failed to get block hash");

    let mut headers = Vec::with_capacity(
        usize::try_from(init_config.num_of_blcoks_to_submit)
            .expect("Error on converting num_of_blocks_to_submit to usize"),
    );
    let mut current_header = bitcoin_client
        .get_aux_block_header(&header_hash)
        .expect("Failed to get initial block header")
        .0;

    headers.push(current_header.clone());

    for _ in 1..init_config.num_of_blcoks_to_submit {
        let prev_hash = BlockHash::from_byte_array(current_header.prev_block_hash.0);
        current_header = bitcoin_client
            .get_aux_block_header(&prev_hash)
            .expect("Failed to get previous block header")
            .0;
        headers.push(current_header.clone());
    }

    headers.reverse();

    let genesis_block_height = init_config.init_height - init_config.num_of_blcoks_to_submit + 1;

    let args = InitArgs {
        genesis_block_hash: headers[0].block_hash(),
        genesis_block_height,
        skip_pow_verification: init_config.skip_pow_verification,
        gc_threshold: init_config.gc_threshold,
        network: init_config.network,
        submit_blocks: headers,
    };

    info!(
        "Init args: {}",
        serde_json::to_string(&args).unwrap_or_else(|_| "<failed to serialize args>".into())
    );

    near_client
        .init_contract(&args)
        .await
        .expect("Failed to init contract");
}
```
