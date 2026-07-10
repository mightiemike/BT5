### Title
Unnecessary `#[trusted_relayer]` Restriction on `submit_blocks` Blocks Any Unprivileged Account from Submitting Valid Headers - (File: `contract/src/lib.rs`)

---

### Summary

`submit_blocks` is the sole write path for advancing the BTC light client's canonical chain. It is gated by `#[trusted_relayer]`, which rejects any caller that is neither a registered active relayer nor holds the `UnrestrictedSubmitBlocks` or `DAO` bypass role. The function body, however, requires nothing from the caller beyond a sufficient NEAR deposit and cryptographically valid headers: PoW is verified internally, storage cost is charged to the caller, and no caller-specific state is read or written. The restriction is therefore unnecessary for correctness or spam prevention. If the trusted-relayer set becomes empty or all relayers go inactive, no unprivileged account can advance the chain, causing the light client to stall and all downstream SPV proofs to silently return stale or failing results.

---

### Finding Description

`submit_blocks` carries three attributes:

```rust
#[payable]
#[pause]
#[trusted_relayer]
pub fn submit_blocks(
    &mut self,
    #[serializer(borsh)] headers: Vec<BlockHeader>,
) -> PromiseOrValue<()> {
``` [1](#0-0) 

The `#[trusted_relayer]` proc-macro is configured at the `impl` level:

```rust
#[trusted_relayer(
    bypass_roles(Role::DAO, Role::UnrestrictedSubmitBlocks),
    manager_roles(Role::DAO, Role::RelayerManager),
    config_roles(Role::DAO)
)]
``` [2](#0-1) 

This means every call to `submit_blocks` is rejected with `"Relayer is not active"` unless the predecessor is either (a) a registered, active trusted relayer or (b) holds `UnrestrictedSubmitBlocks` or `DAO`. The test suite explicitly confirms this rejection path: [3](#0-2) 

Inside the function body, the caller identity is never consulted again. The function:
1. Iterates headers and calls `submit_block_header`, which validates PoW and chain linkage.
2. Calls `run_mainchain_gc` — itself a public, unrestricted function.
3. Computes the storage delta and requires the caller to cover it via attached deposit.
4. Refunds any excess deposit to `env::predecessor_account_id()`. [4](#0-3) 

None of these steps use the caller's identity for any security-relevant decision. The PoW check is the only gate that matters for header validity, and it is applied unconditionally inside `submit_block_header`. The deposit requirement is the only spam deterrent, and it is applied unconditionally. The trusted-relayer check is therefore a redundant, caller-identity-based restriction that provides no additional security property.

---

### Impact Explanation

The light client's canonical chain can only advance through `submit_blocks`. If the trusted-relayer set is empty or all relayers are inactive, no unprivileged NEAR account can submit valid headers, even with correct PoW and a sufficient deposit. The chain tip stored in `mainchain_tip_blockhash` freezes at the last submitted height.

Downstream consequences:

- `verify_transaction_inclusion` and `verify_transaction_inclusion_v2` look up the tip to compute confirmation depth. With a frozen tip, any transaction in a block mined after the freeze will either fail the `"block does not belong to the current main chain"` check or fail the confirmation-count check, returning `false` or panicking. [5](#0-4) 

- Any cross-chain bridge or dApp that calls `verify_transaction_inclusion_v2` to gate fund releases will permanently block withdrawals for transactions confirmed after the freeze, with no recourse for the end user.

The corrupted invariant is: `mainchain_tip_blockhash` no longer tracks the Bitcoin chain tip, breaking the trust-minimized guarantee the contract is designed to provide.

---

### Likelihood Explanation

The trusted-relayer set can become empty or fully inactive through ordinary operational events that require no attacker privilege:

- All registered relayers voluntarily stop staking or let their stake expire.
- A `RelayerManager`/`DAO` action rejects all relayer applications.
- A network partition or operational outage takes all relayers offline simultaneously.

No key compromise, social engineering, or malicious maintainer is required. The `UnrestrictedSubmitBlocks` bypass role exists precisely because the designers anticipated scenarios where the relayer set is insufficient, but granting that role requires a `DAO` or super-admin action, which may itself be unavailable in a governance crisis.

---

### Recommendation

Remove the `#[trusted_relayer]` attribute from `submit_blocks`. The function's internal PoW validation and deposit requirement are sufficient to ensure only valid, paid-for headers are accepted, regardless of caller identity. This matches the design of `run_mainchain_gc`, which is already open to any caller. If the trusted-relayer staking mechanism is intentional for accountability reasons, document it explicitly and provide an always-open fallback path (e.g., a separate `submit_blocks_permissionless` that any account can call with a higher deposit).

---

### Proof of Concept

1. Deploy the contract with `skip_pow_verification = false` and a non-empty genesis.
2. Ensure no account holds `UnrestrictedSubmitBlocks` or `DAO`, and no trusted relayer is registered/active.
3. Obtain a valid next Bitcoin block header (correct `prev_block_hash`, valid PoW, correct `bits`).
4. Call `submit_blocks` from any NEAR account with a sufficient deposit.
5. Observe the transaction fails with `"Relayer is not active"` — confirmed by the existing test at `contract/tests/test_basics.rs:653–710`.
6. Call `verify_transaction_inclusion_v2` for a transaction in the valid block from step 3.
7. Observe it panics with `"block does not belong to the current main chain"` because the tip was never advanced.

The chain tip is permanently frozen at the last height submitted by an authorized relayer, and no unprivileged account can unfreeze it. [6](#0-5) [7](#0-6)

### Citations

**File:** contract/src/lib.rs (L120-124)
```rust
#[trusted_relayer(
    bypass_roles(Role::DAO, Role::UnrestrictedSubmitBlocks),
    manager_roles(Role::DAO, Role::RelayerManager),
    config_roles(Role::DAO)
)]
```

**File:** contract/src/lib.rs (L166-172)
```rust
    #[payable]
    #[pause]
    #[trusted_relayer]
    pub fn submit_blocks(
        &mut self,
        #[serializer(borsh)] headers: Vec<BlockHeader>,
    ) -> PromiseOrValue<()> {
```

**File:** contract/src/lib.rs (L173-197)
```rust
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
```

**File:** contract/src/lib.rs (L294-308)
```rust
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
```

**File:** contract/src/lib.rs (L346-368)
```rust
    #[pause]
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
