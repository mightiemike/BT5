### Title
Unchecked Promise Return Value in `submit_blocks` Silently Discards Excess NEAR Refund on Transfer Failure - (File: `contract/src/lib.rs`)

---

### Summary
The `submit_blocks` function refunds excess NEAR deposit to the caller via a `Promise::transfer()`, but the result of that promise is never verified through a callback. If the transfer receipt fails, the excess NEAR tokens are permanently locked in the contract with no recovery path, and the block submission itself is considered successful — an exact structural analog to the `refundSuccess` unchecked-return-value pattern in the reference report.

---

### Finding Description

In `submit_blocks()`, after computing the required storage deposit, any excess NEAR is returned to the caller: [1](#0-0) 

```rust
let refund = amount.saturating_sub(required_deposit);
if refund > NearToken::from_near(0) {
    Promise::new(env::predecessor_account_id())
        .transfer(refund)
        .into()
} else {
    PromiseOrValue::Value(())
}
```

In NEAR Protocol, when a function returns a `Promise`, that promise executes as a **separate asynchronous receipt** after the main function's state changes are committed. The success or failure of the transfer receipt is never fed back to the contract — there is no `.then(callback)` attached. If the transfer receipt fails (e.g., the predecessor account is a contract that is deleted between the call receipt and the transfer receipt, or a protocol-level failure occurs), the excess NEAR tokens remain in the contract's balance with no mechanism to recover or re-issue them.

The block headers are already committed to state at this point: [2](#0-1) 

The state change (header submission) is irreversible regardless of whether the refund succeeds.

---

### Impact Explanation

Excess NEAR tokens paid by a relayer or any unprivileged NEAR caller are permanently locked in the `BtcLightClient` contract if the transfer receipt fails. There is no admin withdrawal function, no retry mechanism, and no callback to detect the failure. The caller loses their overpayment silently.

---

### Likelihood Explanation

Low-to-moderate. A NEAR `Promise::transfer()` to a live EOA-style account will almost always succeed. However, the risk is concrete when:
- The caller is a **smart contract** (e.g., a relayer wrapper contract) that is deleted or becomes inaccessible between the call receipt and the transfer receipt execution.
- Any future protocol-level condition causes the transfer to fail.

The absence of a callback means there is zero observability or recovery regardless of cause.

---

### Recommendation

Attach a `.then(Self::ext(env::current_account_id()).on_refund_complete())` callback to the transfer promise and implement `on_refund_complete` to log or re-queue failed refunds. Alternatively, adopt a pull-payment pattern: track owed refunds in contract state and expose a `claim_refund()` method so callers can withdraw their excess deposit explicitly.

---

### Proof of Concept

1. Deploy the contract and call `submit_blocks` from a NEAR smart contract account, attaching a deposit larger than the required storage cost.
2. Before the transfer receipt is processed (in the same or next block), delete the calling contract account via a separate transaction.
3. The transfer receipt fails because the destination account no longer exists.
4. The excess NEAR remains in `BtcLightClient`'s balance. The block headers are already in state. No error is surfaced to the original caller. [3](#0-2)

### Citations

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
