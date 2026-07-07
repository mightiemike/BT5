### Title
Parallel `WithdrawCollateral` Execution Paths Enforce Inconsistent Fee Obligations — (`File: core/contracts/EndpointTx.sol`)

### Summary
`EndpointTx` exposes two parallel execution paths for the `WithdrawCollateral` transaction type. The fast (sequencer-submitted) path charges the product-specific `withdrawFeeX18` from the subaccount balance before executing the withdrawal. The slow mode (user-submitted) path executes the identical `clearinghouse.withdrawCollateral` call without charging `withdrawFeeX18`. Any registered subaccount holder can exploit this inconsistency to bypass the withdrawal fee entirely.

### Finding Description
`processTransactionImpl` handles `WithdrawCollateral` at lines 413–436 of `EndpointTx.sol`. After validating the signed transaction and nonce, it charges the fee:

```solidity
chargeFee(
    signedTx.tx.sender,
    spotEngine.getConfig(signedTx.tx.productId).withdrawFeeX18,
    signedTx.tx.productId
);
clearinghouse.withdrawCollateral(...);
```

`processSlowModeTransactionImpl` handles the same `WithdrawCollateral` type at lines 217–229. It performs only an address-match check (`validateSender`) and then calls `clearinghouse.withdrawCollateral` directly — no `withdrawFeeX18` is charged:

```solidity
validateSender(txn.sender, sender);
clearinghouse.withdrawCollateral(
    txn.sender, txn.productId, txn.amount, address(0), nSubmissions
);
```

The two paths write to the same `clearinghouse` state and produce the same withdrawal outcome, but only one enforces the fee invariant. The `chargeFee` call in the fast path credits `sequencerFee[productId]`; the slow mode path leaves it uncredited.

`SpotEngine.initialize` sets `withdrawFeeX18: ONE` ($1 in 18-decimal fixed-point) for the quote product. For other products the value is governance-configurable and can be higher.

The slow mode submission path (`submitSlowModeTransactionImpl`, lines 332–385) charges only the flat `SLOW_MODE_FEE = 1_000_000` ($1 in 6-decimal USDC) from the caller's wallet. This fee goes to `slowModeFees`, a separate accounting bucket from `sequencerFee[productId]`. The two fees are not fungible substitutes.

### Impact Explanation
The broken invariant is: *every `WithdrawCollateral` execution must credit `sequencerFee[productId]` by `withdrawFeeX18`*. Via the slow mode path, this credit never occurs. The concrete corrupted state is `sequencerFee[productId]` — it is under-credited by `withdrawFeeX18` per bypassed withdrawal. For products where