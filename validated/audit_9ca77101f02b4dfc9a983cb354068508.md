### Title
`PauseManager` Can Block All SPV Proof Verification With No Bypass Path — (`contract/src/lib.rs`)

### Summary
The `verify_transaction_inclusion` and `verify_transaction_inclusion_v2` functions carry a plain `#[pause]` attribute with no `except(roles(...))` bypass. When a `PauseManager` pauses the contract, every caller — including privileged accounts — is unconditionally blocked from calling these functions. Downstream dApps and cross-chain bridges that depend on SPV proofs from this light client are frozen until the contract is unpaused by the same privileged role.

### Finding Description
The contract derives `Pausable` from `near_plugins` and designates `Role::PauseManager` as the pause authority:

```rust
#[derive(Pausable, Upgradable, PanicOnDefault)]
#[pausable(manager_roles(Role::PauseManager))]
``` [1](#0-0) 

Both public verification entry points are gated by a bare `#[pause]` with no role-based escape hatch:

```rust
#[pause]
pub fn verify_transaction_inclusion(&self, ...) -> bool { ... }
``` [2](#0-1) 

```rust
#[pause]
pub fn verify_transaction_inclusion_v2(&self, ...) -> bool { ... }
``` [3](#0-2) 

The contract's own `run_mainchain_gc` demonstrates the correct pattern — it defines an explicit bypass role so privileged callers can still operate while the contract is paused:

```rust
#[pause(except(roles(Role::UnrestrictedRunGC)))]
pub fn run_mainchain_gc(&mut self, batch_size: u64) { ... }
``` [4](#0-3) 

No equivalent `UnrestrictedVerify` (or similar) role exists in the `Role` enum, and neither verification function uses `except(roles(...))`. [5](#0-4) 

### Impact Explanation
**Impact: High.**
`verify_transaction_inclusion_v2` is the primary production SPV endpoint consumed by cross-chain bridges, DEXes, and any dApp that gates asset release on Bitcoin transaction finality. When the contract is paused, every such consumer receives a hard revert on every proof call. Funds locked in dependent contracts behind a "verify then release" pattern cannot be released until the `PauseManager` unpauses — a unilateral, indefinite freeze of all downstream settlement.

### Likelihood Explanation
**Likelihood: Low.**
Triggering the pause requires a `PauseManager`-role account to call `pa_pause_feature`. This is a privileged action and mirrors the exact likelihood profile of the original M-08 report (guard-initiated pause). The risk is not theoretical: the role exists, is grantable, and the pause path is fully implemented.

### Recommendation
Apply the same bypass-role pattern already used on `run_mainchain_gc` to both verification functions. Add a new role (e.g., `UnrestrictedVerify`) to the `Role` enum and annotate both functions:

```rust
#[pause(except(roles(Role::UnrestrictedVerify)))]
pub fn verify_transaction_inclusion(&self, ...) -> bool { ... }

#[pause(except(roles(Role::UnrestrictedVerify)))]
pub fn verify_transaction_inclusion_v2(&self, ...) -> bool { ... }
```

Alternatively, since both functions are read-only view calls that do not mutate state, consider removing `#[pause]` from them entirely — pausing a light client should halt new header ingestion (`submit_blocks`), not proof reads.

### Proof of Concept
1. Deploy the contract and grant `Role::PauseManager` to account `pause_admin`.
2. `pause_admin` calls `pa_pause_feature("verify_transaction_inclusion_v2")` (or the global pause).
3. Any unprivileged NEAR account (or a downstream bridge contract) calls `verify_transaction_inclusion_v2` with a valid proof.
4. The call reverts unconditionally — the `#[pause]` macro panics before the function body executes.
5. No role exists that would allow bypassing this check; the bridge is frozen until `pause_admin` calls `pa_unpause_feature`.

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

**File:** contract/src/lib.rs (L87-88)
```rust
#[derive(Pausable, Upgradable, PanicOnDefault)]
#[pausable(manager_roles(Role::PauseManager))]
```

**File:** contract/src/lib.rs (L287-288)
```rust
    #[pause]
    pub fn verify_transaction_inclusion(&self, #[serializer(borsh)] args: ProofArgs) -> bool {
```

**File:** contract/src/lib.rs (L346-347)
```rust
    #[pause]
    pub fn verify_transaction_inclusion_v2(&self, #[serializer(borsh)] args: ProofArgsV2) -> bool {
```

**File:** contract/src/lib.rs (L376-377)
```rust
    #[pause(except(roles(Role::UnrestrictedRunGC)))]
    pub fn run_mainchain_gc(&mut self, batch_size: u64) {
```
