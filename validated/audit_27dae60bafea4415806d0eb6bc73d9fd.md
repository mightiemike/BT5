### Title
Excess Deposit Permanently Locked in `AddressRegistrar::register` on Successful Registration - (`runtime/near-wallet-contract/implementation/address-registrar/src/lib.rs`)

### Summary
The `register` function in the `AddressRegistrar` contract accepts any deposit `>= required_deposit` (a `<` guard, not `==`), but on a successful `Entry::Vacant` insertion it distributes exactly `required_deposit` worth of storage and silently retains the remainder. The excess yoctoNEAR is permanently locked in the contract with no withdrawal path. The collision branch (`Entry::Occupied`) correctly refunds the full deposit, making the asymmetry clear. Because `env::storage_byte_cost()` is a live protocol parameter that has already changed once (genesis: 10²⁰ yN/byte → current: 10¹⁹ yN/byte), any user who pre-computes the required deposit before a protocol-version boundary and submits after it will silently lose the delta.

### Finding Description
In `register`:

```rust
let required_deposit =
    NearToken::from_yoctonear(env::storage_byte_cost().as_yoctonear() * bytes_to_store);
let given_deposit = env::attached_deposit();
if given_deposit < required_deposit {          // ← allows overpayment
    env::panic_str(&message);
}
// ...
match self.addresses.entry(address) {
    Entry::Vacant(entry) => {
        entry.insert(account_id);
        // ← no refund of (given_deposit - required_deposit)
        Some(address)
    }
    Entry::Occupied(entry) => {
        // ← full refund here, but not in the success branch
        env::promise_batch_action_transfer(refund_promise, given_deposit);
        None
    }
}
```

The contract only writes `bytes_to_store = 20 + account_id.len()` bytes of state, costing exactly `required_deposit`. Any amount above that is absorbed into the contract's own balance and is irrecoverable because `AddressRegistrar` has no owner-withdrawal or sweep function.

Two concrete trigger paths exist:

1. **Direct overpayment**: A caller sends `given_deposit > required_deposit` (e.g., rounding up, or using a stale off-chain estimate). The excess is locked immediately.
2. **Protocol-upgrade timing**: A user queries `storage_byte_cost` at block *N* (value *C*), constructs a transaction with deposit `C * bytes_to_store`, and the transaction executes at block *N+k* after a protocol upgrade has lowered the cost to *C'*. The contract now only needs `C' * bytes_to_store` but keeps the full `C * bytes_to_store`, locking `(C - C') * bytes_to_store` yoctoNEAR. The mainnet genesis → current transition already demonstrates a 10× reduction in `storage_amount_per_byte` (10²⁰ → 10¹⁹), so this path is historically reachable.

### Impact Explanation
Any yoctoNEAR above `required_deposit` sent to a successful `register` call is permanently locked in the `AddressRegistrar` singleton. There is no owner key, no `withdraw`, and no sweep function. The contract is immutable once deployed. For a 10× drop in `storage_byte_cost` and a 40-byte entry, the locked amount per call would be `40 × (10²⁰ − 10¹⁹) = 3.6 × 10²¹ yN ≈ 3.6 mNEAR`. Aggregated across all registrations that straddle a protocol upgrade boundary, the total locked value scales linearly with the number of affected calls.

### Likelihood Explanation
- The `storage_byte_cost` is a live protocol parameter (`env::storage_byte_cost()` is evaluated at execution time, not at signing time), so any protocol upgrade that lowers it creates a window where pre-signed transactions overpay.
- Wallets and tooling that pre-compute the deposit from an RPC snapshot of `storage_amount_per_byte` and add no buffer will hit path 1 if they round up, or path 2 if a protocol upgrade lands between signing and inclusion.
- The `AddressRegistrar` is the singleton used by all ETH-implicit account registrations on NEAR mainnet, so the affected population is every user who registers a named account mapping.

### Recommendation
Refund the excess deposit in the `Entry::Vacant` branch, mirroring the `Entry::Occupied` branch:

```rust
Entry::Vacant(entry) => {
    entry.insert(account_id);
    let excess = given_deposit.as_yoctonear()
        .saturating_sub(required_deposit.as_yoctonear());
    if excess > 0 {
        let refund_promise = env::promise_batch_create(&env::predecessor_account_id());
        env::promise_batch_action_transfer(
            refund_promise,
            NearToken::from_yoctonear(excess),
        );
    }
    Some(address)
}
```

Alternatively, enforce an exact-payment invariant (`given_deposit != required_deposit → panic`) to make the expected amount explicit to callers.

### Proof of Concept
1. Read the current `storage_byte_cost` from the chain (e.g., 10¹⁹ yN/byte).
2. Compute `required = 10¹⁹ × (20 + len("alice.near")) = 10¹⁹ × 30 = 3 × 10²⁰ yN`.
3. Call `register("alice.near")` with `attached_deposit = 4 × 10²⁰ yN` (33% overpayment).
4. The call succeeds, `alice.near` is registered, and `1 × 10²⁰ yN` (0.1 NEAR) is permanently locked in the `AddressRegistrar` contract balance with no recovery path.

The same outcome occurs automatically for any caller whose deposit was computed before a protocol upgrade that reduced `storage_amount_per_byte`, because `env::storage_byte_cost()` reflects the post-upgrade value at execution time while the deposit reflects the pre-upgrade value. [1](#0-0) [2](#0-1)

### Citations

**File:** runtime/near-wallet-contract/implementation/address-registrar/src/lib.rs (L48-61)
```rust
        // Must store the address and the account id
        let bytes_to_store = 20 + (account_id.len() as u128);
        let required_deposit =
            NearToken::from_yoctonear(env::storage_byte_cost().as_yoctonear() * bytes_to_store);
        let given_deposit = env::attached_deposit();
        // The caller must pay for the storage cost of registering.
        if given_deposit < required_deposit {
            let message = format!(
                "Insufficient deposit to cover storage cost. Given={} Expected={}",
                given_deposit.as_yoctonear(),
                required_deposit.as_yoctonear(),
            );
            env::panic_str(&message);
        }
```

**File:** runtime/near-wallet-contract/implementation/address-registrar/src/lib.rs (L65-85)
```rust
        match self.addresses.entry(address) {
            Entry::Vacant(entry) => {
                let address = format!("0x{}", hex::encode(address));
                let log_message = format!("Added entry {} -> {}", address, account_id);
                entry.insert(account_id);
                env::log_str(&log_message);
                Some(address)
            }
            Entry::Occupied(entry) => {
                let log_message = format!(
                    "Address collision between {} and {}. Keeping the former.",
                    entry.get(),
                    account_id
                );
                env::log_str(&log_message);
                // Transfer the deposit back to the caller since no storage was updated.
                let refund_promise = env::promise_batch_create(&env::predecessor_account_id());
                env::promise_batch_action_transfer(refund_promise, given_deposit);
                None
            }
        }
```
