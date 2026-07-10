### Title
`UnrestrictedSubmitBlocks` role placed in wrong attribute, rendering it unable to bypass the pause guard — (`contract/src/lib.rs`)

### Summary

`Role::UnrestrictedSubmitBlocks` is documented as "Allows to use contract API even after contract is paused," but it is wired into the `trusted_relayer` macro's `bypass_roles` parameter instead of the `#[pause]` attribute's `except(roles(...))` clause. When the contract is paused, `submit_blocks` is blocked for every caller — including accounts that hold `UnrestrictedSubmitBlocks` — because the `#[pause]` guard on that function has no exception list at all. The role silently bypasses the staked-relayer check instead of the pause check, which is the opposite of its stated purpose.

### Finding Description

`Role::UnrestrictedSubmitBlocks` is declared with the comment "Allows to use contract API even after contract is paused": [1](#0-0) 

The role is wired into the `trusted_relayer` macro's `bypass_roles` parameter at the `impl` block level: [2](#0-1) 

`submit_blocks` carries a bare `#[pause]` with no `except` clause: [3](#0-2) 

By contrast, `run_mainchain_gc` correctly places its counterpart role in the `#[pause]` exception list: [4](#0-3) 

The `bypass_roles` parameter of `trusted_relayer` controls who may call `submit_blocks` without being a staked relayer — it has no effect on the `near-plugins` `#[pause]` guard. The two checks are independent. Because `#[pause]` on `submit_blocks` has no `except(roles(...))` clause, the pause guard rejects every caller unconditionally when the contract is paused, regardless of whether they hold `UnrestrictedSubmitBlocks`.

The integration tests confirm the misuse: every test helper that grants `UnrestrictedSubmitBlocks` documents it as passing the `#[trusted_relayer]` guard, not the pause guard: [5](#0-4) 

### Impact Explanation

When a `PauseManager` pauses the contract, `submit_blocks` becomes completely unreachable. No account — including those explicitly granted `UnrestrictedSubmitBlocks` for emergency use — can submit Bitcoin block headers. The light client stops tracking the chain. Any downstream NEAR contract that calls `verify_transaction_inclusion` or `verify_transaction_inclusion_v2` against blocks mined after the pause will receive stale or missing data, breaking SPV proof verification for the duration of the pause.

### Likelihood Explanation

The `PauseManager` role exists precisely to pause the contract during emergencies or upgrades. The `UnrestrictedSubmitBlocks` role was introduced to allow a privileged relayer to keep the chain synchronized even during such events. Because the role is wired to the wrong guard, every pause event silently disables the emergency bypass, making the intended safety valve inoperative in exactly the scenarios it was designed for.

### Recommendation

Move `Role::UnrestrictedSubmitBlocks` from `trusted_relayer`'s `bypass_roles` into the `#[pause]` exception list on `submit_blocks`, mirroring the correct pattern used by `run_mainchain_gc`:

```rust
// Before (wrong):
#[pause]
#[trusted_relayer]
pub fn submit_blocks(...) { ... }

// After (correct):
#[pause(except(roles(Role::UnrestrictedSubmitBlocks)))]
#[trusted_relayer]
pub fn submit_blocks(...) { ... }
```

If bypassing the staked-relayer check is also required, introduce a dedicated role (e.g., `UnstakedRelayer`) for that purpose and keep it in `bypass_roles`.

### Proof of Concept

1. Deploy the contract and initialize it.
2. Grant `UnrestrictedSubmitBlocks` to account `alice`.
3. Call `pa_pause` (via a `PauseManager` account) to pause the contract.
4. Have `alice` call `submit_blocks` with a valid Bitcoin header.
5. **Observed:** the call panics with the `near-plugins` pause error — `UnrestrictedSubmitBlocks` provides no exception.
6. **Expected:** `alice`'s call succeeds because she holds the role documented as bypassing the pause.

The `#[pause]` guard fires before any `trusted_relayer` logic, and since `submit_blocks` has no `except(roles(...))` clause, the role never has a chance to take effect. [2](#0-1) [3](#0-2)

### Citations

**File:** contract/src/lib.rs (L43-44)
```rust
    /// Allows to use contract API even after contract is paused
    UnrestrictedSubmitBlocks,
```

**File:** contract/src/lib.rs (L120-124)
```rust
#[trusted_relayer(
    bypass_roles(Role::DAO, Role::UnrestrictedSubmitBlocks),
    manager_roles(Role::DAO, Role::RelayerManager),
    config_roles(Role::DAO)
)]
```

**File:** contract/src/lib.rs (L166-169)
```rust
    #[payable]
    #[pause]
    #[trusted_relayer]
    pub fn submit_blocks(
```

**File:** contract/src/lib.rs (L376-377)
```rust
    #[pause(except(roles(Role::UnrestrictedRunGC)))]
    pub fn run_mainchain_gc(&mut self, batch_size: u64) {
```

**File:** contract/tests/test_basics.rs (L38-40)
```rust
    /// Grant the `UnrestrictedSubmitBlocks` role to an account so it passes the
    /// `#[trusted_relayer]` guard on `submit_blocks`. The contract itself is the
    /// super admin (set during `init`), so it can grant any role.
```
