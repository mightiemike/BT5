### Title
Missing Role Grants in `init()` Leaves `submit_blocks` and All Privileged Functions Permanently Inaccessible - (File: contract/src/lib.rs)

### Summary
The `init()` function grants super-admin status only to the contract account itself (`env::current_account_id()`), but never grants any operational role (`UnrestrictedSubmitBlocks`, `DAO`, `RelayerManager`, `PauseManager`, `CodeStager`, `CodeDeployer`, `DurationManager`) to any human address. Because `submit_blocks` is gated by `#[trusted_relayer]` whose only bypass paths require `Role::DAO` or `Role::UnrestrictedSubmitBlocks`, and relayer registration requires `Role::DAO` or `Role::RelayerManager`, the contract's sole write entrypoint is unreachable by any external caller immediately after deployment. Tests mask this by calling `acl_grant_role` as a manual post-`init` step, which is absent from any deployment script.

### Finding Description
`init()` performs exactly one ACL operation:

```rust
contract.acl_init_super_admin(env::current_account_id())
``` [1](#0-0) 

No role is granted to any human address. The `submit_blocks` method carries two guards:

```rust
#[trusted_relayer(
    bypass_roles(Role::DAO, Role::UnrestrictedSubmitBlocks),
    manager_roles(Role::DAO, Role::RelayerManager),
    config_roles(Role::DAO)
)]
``` [2](#0-1) 

To reach `submit_blocks`, a caller must either (a) hold `Role::DAO` or `Role::UnrestrictedSubmitBlocks` (bypass path), or (b) be a registered trusted relayer. Registering a relayer requires `Role::DAO` or `Role::RelayerManager`. Since `init()` grants none of these roles to any external account, both paths are closed at deployment time.

`InitArgs` contains no `admin` or `relayer` field, so there is no mechanism inside `init()` to specify a privileged address:

```rust
pub struct InitArgs {
    pub genesis_block_hash: H256,
    pub genesis_block_height: u64,
    pub skip_pow_verification: bool,
    pub gc_threshold: u64,
    pub network: Network,
    pub submit_blocks: Vec<Header>,
}
``` [3](#0-2) 

Every integration test works only because it calls `acl_grant_role` as a separate step after `init()`, using the contract account's implicit super-admin privilege:

```rust
let user_account = sandbox.dev_create_account().await?;
grant_relayer_role(&contract, &user_account).await?;
``` [4](#0-3) 

This post-`init` grant is a test-only artifact. There are no deployment scripts in the repository that perform it in production.

The same gap applies to all other privileged roles: `PauseManager` (pause/unpause), `DAO`/`CodeStager`/`CodeDeployer`/`DurationManager` (upgrades), and `RelayerManager` (relayer management). [5](#0-4) 

### Impact Explanation
**High**: `submit_blocks` is the sole write entrypoint of the light client. Without it, no Bitcoin block headers can be submitted to the contract after deployment. The on-chain chain tip freezes at the genesis block submitted during `init()`. Any downstream consumer calling `verify_transaction_inclusion` or `verify_transaction_inclusion_v2` will operate against a stale, non-advancing chain, making all SPV proofs for post-genesis transactions permanently unverifiable. Additionally, no one can pause the contract in an emergency, and no one can upgrade it, because `PauseManager`, `DAO`, `CodeStager`, `CodeDeployer`, and `DurationManager` are also ungranted.

### Likelihood Explanation
**High**: The `init()` function accepts no admin or relayer address parameter, so there is no in-constructor path to grant roles. No deployment script exists in the repository to perform the grant post-deployment. The only working pattern in the codebase is the test-local `grant_relayer_role` helper, which is invisible to a production deployer following the contract's own `init` interface. The omission is structural, not incidental.

### Recommendation
Add an `admin` (and optionally a `relayer`) `AccountId` field to `InitArgs`. Inside `init()`, after `acl_init_super_admin`, grant the required roles:

```rust
contract.acl_grant_role(Role::DAO.into(), &args.admin);
contract.acl_grant_role(Role::PauseManager.into(), &args.admin);
contract.acl_grant_role(Role::RelayerManager.into(), &args.admin);
contract.acl_grant_role(Role::UnrestrictedSubmitBlocks.into(), &args.relayer);
```

This mirrors the fix applied in the referenced Infrared PRs (`_grantRole(KEEPER_ROLE, _keeper)` inside `initialize()`).

### Proof of Concept
1. Deploy the contract and call `init` with valid genesis data (no extra steps).
2. From any NEAR account (including the deployer's separate account), call `submit_blocks` with a valid next block header and the required storage deposit.
3. Observe the transaction fails with `"Relayer is not active"` — confirmed by the existing test `test_unauthorized_account_cannot_submit_blocks` which documents exactly this failure mode for any account that has not been manually granted a role after `init`. [6](#0-5) 

The contract's chain tip remains permanently frozen at the genesis block submitted during `init`, and all SPV verification calls against post-genesis Bitcoin blocks will fail.

### Citations

**File:** contract/src/lib.rs (L85-95)
```rust
#[access_control(role_type(Role))]
#[near(contract_state)]
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

**File:** contract/src/lib.rs (L120-124)
```rust
#[trusted_relayer(
    bypass_roles(Role::DAO, Role::UnrestrictedSubmitBlocks),
    manager_roles(Role::DAO, Role::RelayerManager),
    config_roles(Role::DAO)
)]
```

**File:** contract/src/lib.rs (L147-152)
```rust
        // Make the contract itself super admin. This allows us to grant any role in the
        // constructor.
        near_sdk::require!(
            contract.acl_init_super_admin(env::current_account_id()),
            "Failed to initialize super admin",
        );
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

**File:** contract/tests/test_basics.rs (L86-88)
```rust
        let user_account = sandbox.dev_create_account().await?;
        grant_relayer_role(&contract, &user_account).await?;

```

**File:** contract/tests/test_basics.rs (L699-708)
```rust
        assert!(
            !outcome.is_success(),
            "Expected submit_blocks to fail for an account without roles, but it succeeded"
        );

        let failure_message = format!("{:?}", outcome.failures());
        assert!(
            failure_message.contains("Relayer is not active"),
            "Expected failure message to contain 'Relayer is not active', but got: {failure_message}",
        );
```
