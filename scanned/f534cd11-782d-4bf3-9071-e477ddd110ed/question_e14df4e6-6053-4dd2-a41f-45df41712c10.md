[File: 'File Name: core/contracts/WithdrawPool.sol -> Scope: Critical. Transaction manipulation that changes order parameters, recipient routing, price or amount semantics, liquidation terms, or settlement outcomes in a way that transfers value incorrectly.'] [Function: BaseWithdrawPool.fastWithdrawalFeeAmount] Can an attacker under the precondition that int256 multiplier = int256(10**(MAX_DECIMALS - uint8(decimals))) is computed as int256 but then cast to int128 via int128(multiplier) in the return statement feeX18 / int128(multiplier) trigger submitFastWith

### Citations

**File:** core/contracts/BaseWithdrawPool.sol (L44-79)
```text
    function resolveFastWithdrawal(bytes calldata transaction)
        internal
        pure
        returns (
            uint32 productId,
            address sendTo,
            uint128 amount
        )
    {
        IEndpoint.TransactionType txType = IEndpoint.TransactionType(
            uint8(transaction[0])
        );
        if (txType == IEndpoint.TransactionType.WithdrawCollateral) {
            IEndpoint.SignedWithdrawCollateral memory signedTx = abi.decode(
                transaction[1:],
                (IEndpoint.SignedWithdrawCollateral)
            );
            return (
                signedTx.tx.productId,
                address(uint160(bytes20(signedTx.tx.sender))),
                signedTx.tx.amount
            );
        }
        if (txType == IEndpoint.TransactionType.WithdrawCollateralV2) {
            IEndpoint.SignedWithdrawCollateralV2 memory signedTx = abi.decode(
                transaction[1:],
                (IEndpoint.SignedWithdrawCollateralV2)
            );
            // V2 appendix is intentionally ignored until fast-withdraw features use it.
            address resolvedSendTo = signedTx.tx.sendTo == address(0)
                ? address(uint160(bytes20(signedTx.tx.sender)))
                : signedTx.tx.sendTo;
            return (signedTx.tx.productId, resolvedSendTo, signedTx.tx.amount);
        }
        revert(
