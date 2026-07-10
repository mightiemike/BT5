### Title
`UnrestrictedSubmitBlocks` Role Cannot Bypass Pause — `submit_blocks` Permanently Blocked for Role Holders When Contract Is Paused - (File: `contract/src/lib.rs`)

---

### Summary

The `UnrestrictedSubmitBlocks` role is explicitly documented to "allow use of contract API even after contract is paused," but `submit_blocks` is decorated with a bare `#[pause]` that has no exception for this role. The role only bypasses the trusted-relayer staking gate (via `bypass_roles` in the `#[trusted_relayer]` macro), not the pause gate. The role's documented purpose is permanently non-functional — a direct structural analog to the `revertEscrow()` bug where `onlyOwner` and `isSeller || isBuyer` are mutually exclusive.

---

### Finding Description

**Role definition** — the intent is explicit:

```rust
/// Allows to use contract API even after contract is paused
UnrestrictedSubmitBlocks,
``` [1](#0-0) 

**`submit_blocks` — bare `#[pause]`, no exception:**

```rust
#[payable]
#[pause]
#[trusted_relayer]
pub fn submit_blocks(
``` [2](#0-1) 

**`run_mainchain_gc` — the correct pattern that `submit_blocks` is missing:**

```rust
#[pause(except(roles(Role::UnrestrictedRunGC)))]
pub fn run_mainchain_gc(&mut self, batch_size: u64) {
``` [3](#0-2) 

**`trusted_relayer` macro — `bypass_roles` only bypasses the staking/relayer check, not the pause gate:**

```rust
#[trusted_relayer(
    bypass_roles(Role::DAO, Role::UnrestrictedSubmitBlocks),
    manager_roles(Role::DAO, Role::RelayerManager),
    config_roles(Role::DAO)
)]
``` [4](#0-3) 

The contradiction is exact: `UnrestrictedSubmitBlocks` holders are exempted from the trusted-relayer staking requirement (via `bypass_roles`), but they are **not** exempted from the pause check (no `except(roles(...))` on `#[pause]`). The role's only documented purpose — submitting blocks while the contract is paused — is structurally impossible to exercise.

---

### Impact Explanation

When the contract is paused (e.g., during an emergency response), **all** callers of `submit_blocks` are rejected, including accounts that hold `UnrestrictedSubmitBlocks`. The canonical chain state in the light client freezes. Any downstream NEAR contract calling `verify_transaction_inclusion` or `verify_transaction_inclusion_v2` against a block height that has not yet been submitted will receive a panic/false-negative result. Operators who believe they can pause the contract while keeping a trusted relayer path open are operating under a false assumption baked into the role definition itself. [5](#0-4) 

---

### Likelihood Explanation

The bug is unconditional — it is present in every deployment. It manifests the moment any `PauseManager` pauses the contract. No special attacker capability is required; the broken invariant is triggered by normal operational use of the pause mechanism. [6](#0-5) 

---

### Recommendation

Mirror the pattern already used on `run_mainchain_gc`. Change the attribute on `submit_blocks` from:

```rust
#[pause]
```

to:

```rust
#[pause(except(roles(Role::UnrestrictedSubmitBlocks, Role::DAO)))]
```

This aligns the implementation with the documented intent of the role and with the existing correct usage on `run_mainchain_gc`. [3](#0-2) 

---

### Proof of Concept

1. Deploy the contract (any chain feature flag).
2. Grant account `alice.near` the `UnrestrictedSubmitBlocks` role.
3. Grant account `bob.near` the `PauseManager` role.
4. `bob.near` calls the pause method — contract is paused.
5. `alice.near` calls `submit_blocks` with a valid header batch.
6. **Result**: the call panics at the `#[pause]` gate before any relayer or role logic is reached. The `UnrestrictedSubmitBlocks` exemption in `bypass_roles` is never consulted for the pause check.
7. The canonical chain tip is frozen; `verify_transaction_inclusion_v2` for any block submitted after the pause will panic with `"cannot find requested transaction block"`. [7](#0-6)

### Citations

**File:** contract/src/lib.rs (L40-52)
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
```

**File:** contract/src/lib.rs (L120-124)
```rust
#[trusted_relayer(
    bypass_roles(Role::DAO, Role::UnrestrictedSubmitBlocks),
    manager_roles(Role::DAO, Role::RelayerManager),
    config_roles(Role::DAO)
)]
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

**File:** contract/src/lib.rs (L287-323)
```rust
    #[pause]
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

**File:** contract/src/lib.rs (L376-377)
```rust
    #[pause(except(roles(Role::UnrestrictedRunGC)))]
    pub fn run_mainchain_gc(&mut self, batch_size: u64) {
```
