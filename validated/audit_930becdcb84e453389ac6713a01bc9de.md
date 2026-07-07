### Title
Slow-Mode `WithdrawCollateral` (V1) Permanently Freezes Collateral When Subaccount Owner Is Token-Blacklisted — (`core/contracts/EndpointTx.sol`)

---

### Summary

The slow-mode withdrawal path in Nado only supports `WithdrawCollateral` (V1), which has no `sendTo` field and always resolves the transfer destination to the subaccount owner's address. If that address is blacklisted by the collateral token (e.g., USDC), every slow-mode withdrawal attempt silently fails and the user's collateral becomes permanently inaccessible — with no on-chain escape path.

---

### Finding Description

Nado defines two withdrawal transaction types:

- `WithdrawCollateral` (V1): no `sendTo` field; destination is always derived from `sender`.
- `WithdrawCollateralV2`: includes an explicit `sendTo` field, allowing an alternative recipient. [1](#0-0) 

The fast (sequencer-submitted) path in `processTransactionImpl` handles both V1 and V2: [2](#0-1) 

However, the slow-mode path in `processSlowModeTransactionImpl` **only handles V1**, passing `address(0)` as `sendTo`: [3](#0-2) 

In `Clearinghouse.withdrawCollateral`, `sendTo == address(0)` is resolved to the subaccount owner's address: [4](#0-3) 

The resolved address is then used as the destination for `safeTransfer` via `handleWithdrawTransfer`: [5](#0-4) [6](#0-5) 

If the subaccount owner's address is blacklisted by the token contract (e.g., USDC's `transfer` reverts for blacklisted recipients), `safeTransfer` reverts. The outer slow-mode executor catches this silently: [7](#0-6) 

The slow-mode transaction is consumed from the queue (`delete slowModeTxs[...]`), but because the inner call reverted, the balance update in `withdrawCollateral` also reverted — so the user's on-chain balance is preserved but permanently unwithdrawable via slow mode.

A user cannot submit `WithdrawCollateralV2` as a slow-mode transaction either: `submitSlowModeTransactionImpl` would queue it, but `processSlowModeTransactionImpl` has no handler for `WithdrawCollateralV2` and hits `revert()`. [8](#0-7) 

---

### Impact Explanation

A user whose address is blacklisted by the collateral token (e.g., USDC) and who cannot rely on the sequencer (sequencer unresponsive, censoring, or offline) has **no on-chain mechanism** to redirect their collateral to a non-blacklisted address. Every slow-mode V1 withdrawal attempt will fail silently and be consumed. The user's collateral balance is permanently locked in the protocol.

---

### Likelihood Explanation

Two conditions must coincide: (1) the user's address is blacklisted by the token contract, and (2) the sequencer is unavailable or censoring the user's V2 withdrawal. Each condition is individually low-probability, but the combination is realistic — USDC blacklisting does occur, and the slow-mode path exists precisely for sequencer-unavailability scenarios. Severity is **medium**, matching the original report's classification.

---

### Recommendation

Add `WithdrawCollateralV2` handling to `processSlowModeTransactionImpl` in `EndpointTx.sol`, mirroring the existing V1 handler but passing `txn.sendTo` instead of `address(0)`:

```solidity
} else if (txType == IEndpoint.TransactionType.WithdrawCollateralV2) {
    IEndpoint.WithdrawCollateralV2 memory txn = abi.decode(
        transaction[1:],
        (IEndpoint.WithdrawCollateralV2)
    );
    validateSender(txn.sender, sender);
    address resolvedSendTo = txn.sendTo == address(0)
        ? address(uint160(bytes20(txn.sender)))
        : txn.sendTo;
    clearinghouse.withdrawCollateral(
        txn.sender,
        txn.productId,
        txn.amount,
        resolvedSendTo,
        nSubmissions
    );
}
```

This gives users an on-chain escape path to redirect funds to a non-blacklisted address without sequencer cooperation.

---

### Proof of Concept

1. Alice deposits USDC collateral into Nado. Her subaccount owner address is `0xAlice`.
2. Circle blacklists `0xAlice` (e.g., due to a compliance event).
3. The Nado sequencer goes offline (or censors Alice's transactions).
4. Alice calls `Endpoint.submitSlowModeTransaction` with a `WithdrawCollateral` (V1) payload specifying her subaccount and amount. The `requireUnsanctioned` check passes (OFAC sanctions ≠ USDC blacklist).
5. After the 3-day delay, anyone calls `Endpoint.executeSlowModeTransaction`.
6. `processSlowModeTransactionImpl` decodes the V1 tx and calls `clearinghouse.withdrawCollateral(..., address(0), ...)`.
7. `withdrawCollateral` resolves `sendTo` to `0xAlice`, calls `handleWithdrawTransfer` → `safeTransfer(0xAlice, amount)`.
8. USDC's `transfer` reverts because `0xAlice` is blacklisted.
9. The outer `try/catch` in `_executeSlowModeTransaction` swallows the revert; the slow-mode slot is deleted.
10. Alice's protocol balance is intact but she cannot submit V2 via slow mode (no handler), and the sequencer is offline. Funds are permanently inaccessible. [3](#0-2) [9](#0-8)

### Citations

**File:** core/contracts/interfaces/IEndpoint.sol (L80-104)
```text
    struct WithdrawCollateral {
        bytes32 sender;
        uint32 productId;
        uint128 amount;
        uint64 nonce;
    }

    struct SignedWithdrawCollateral {
        WithdrawCollateral tx;
        bytes signature;
    }

    struct CompactSignature {
        bytes32 r;
        bytes32 vs;
    }

    struct WithdrawCollateralV2 {
        bytes32 sender;
        uint32 productId;
        uint128 amount;
        uint64 nonce;
        address sendTo;
        uint128 appendix; // Reserved for forward-compatible withdrawal features.
    }
```

**File:** core/contracts/EndpointTx.sol (L217-229)
```text
        } else if (txType == IEndpoint.TransactionType.WithdrawCollateral) {
            IEndpoint.WithdrawCollateral memory txn = abi.decode(
                transaction[1:],
                (IEndpoint.WithdrawCollateral)
            );
            validateSender(txn.sender, sender);
            clearinghouse.withdrawCollateral(
                txn.sender,
                txn.productId,
                txn.amount,
                address(0),
                nSubmissions
            );
```

**File:** core/contracts/EndpointTx.sol (L327-329)
```text
        } else {
            revert();
        }
```

**File:** core/contracts/EndpointTx.sol (L413-465)
```text
        } else if (txType == IEndpoint.TransactionType.WithdrawCollateral) {
            IEndpoint.SignedWithdrawCollateral memory signedTx = abi.decode(
                transaction[1:],
                (IEndpoint.SignedWithdrawCollateral)
            );
            validateSignedTx(
                signedTx.tx.sender,
                signedTx.tx.nonce,
                transaction,
                signedTx.signature,
                true
            );
            chargeFee(
                signedTx.tx.sender,
                spotEngine.getConfig(signedTx.tx.productId).withdrawFeeX18,
                signedTx.tx.productId
            );
            clearinghouse.withdrawCollateral(
                signedTx.tx.sender,
                signedTx.tx.productId,
                signedTx.tx.amount,
                address(0),
                nSubmissions
            );
        } else if (txType == IEndpoint.TransactionType.WithdrawCollateralV2) {
            IEndpoint.SignedWithdrawCollateralV2 memory signedTx = abi.decode(
                transaction[1:],
                (IEndpoint.SignedWithdrawCollateralV2)
            );
            validateSignedTx(
                signedTx.tx.sender,
                signedTx.tx.nonce,
                transaction,
                signedTx.signature,
                signedTx.tx.sendTo == address(0)
            );
            int128 currentFeeX18 = spotEngine
                .getConfig(signedTx.tx.productId)
                .withdrawFeeX18;
            require(signedTx.feeX18 >= 0);
            require(signedTx.feeX18 <= currentFeeX18);
            chargeFee(
                signedTx.tx.sender,
                signedTx.feeX18,
                signedTx.tx.productId
            );
            clearinghouse.withdrawCollateral(
                signedTx.tx.sender,
                signedTx.tx.productId,
                signedTx.tx.amount,
                signedTx.tx.sendTo,
                nSubmissions
            );
```

**File:** core/contracts/Clearinghouse.sol (L377-385)
```text
    function handleWithdrawTransfer(
        IERC20Base token,
        address to,
        uint128 amount,
        uint64 idx
    ) internal virtual {
        token.safeTransfer(withdrawPool, uint256(amount));
        BaseWithdrawPool(withdrawPool).submitWithdrawal(token, to, amount, idx);
    }
```

**File:** core/contracts/Clearinghouse.sol (L391-421)
```text
    function withdrawCollateral(
        bytes32 sender,
        uint32 productId,
        uint128 amount,
        address sendTo,
        uint64 idx
    ) public virtual onlyEndpoint {
        require(!RiskHelper.isIsolatedSubaccount(sender), ERR_UNAUTHORIZED);
        require(amount <= INT128_MAX, ERR_CONVERSION_OVERFLOW);
        ISpotEngine spotEngine = _spotEngine();
        IERC20Base token = IERC20Base(spotEngine.getConfig(productId).token);
        require(address(token) != address(0));

        if (sendTo == address(0)) {
            sendTo = address(uint160(bytes20(sender)));
        }

        handleWithdrawTransfer(token, sendTo, amount, idx);

        int256 multiplier = int256(10**(MAX_DECIMALS - _decimals(productId)));
        int128 amountRealized = -int128(amount) * int128(multiplier);
        spotEngine.updateBalance(productId, sender, amountRealized);
        spotEngine.assertUtilization(productId);

        IProductEngine.HealthType healthType = sender == X_ACCOUNT
            ? IProductEngine.HealthType.PNL
            : IProductEngine.HealthType.INITIAL;

        require(getHealth(sender, healthType) >= 0, ERR_SUBACCT_HEALTH);
        emit ModifyCollateral(amountRealized, sender, productId);
    }
```

**File:** core/contracts/BaseWithdrawPool.sol (L184-190)
```text
    function handleWithdrawTransfer(
        IERC20Base token,
        address to,
        uint128 amount
    ) internal virtual {
        token.safeTransfer(to, uint256(amount));
    }
```

**File:** core/contracts/Endpoint.sol (L205-227)
```text
            uint256 gasRemaining = gasleft();
            // solhint-disable-next-line no-empty-blocks
            try this.processSlowModeTransaction(txn.sender, txn.tx) {} catch {
                // we need to differentiate between a revert and an out of gas
                // the issue is that in evm every inner call only 63/64 of the
                // remaining gas in the outer frame is forwarded. as a result
                // the amount of gas left for execution is (63/64)**len(stack)
                // and you can get an out of gas while spending an arbitrarily
                // low amount of gas in the final frame. we use a heuristic
                // here that isn't perfect but covers our cases.
                // having gasleft() <= gasRemaining / 2 buys us 44 nested calls
                // before we miss out of gas errors; 1/2 ~= (63/64)**44
                // this is good enough for our purposes

                if (gasleft() <= 250000 || gasleft() <= gasRemaining / 2) {
                    // solhint-disable-next-line no-inline-assembly
                    assembly {
                        invalid()
                    }
                }

                // try return funds now removed
            }
```
