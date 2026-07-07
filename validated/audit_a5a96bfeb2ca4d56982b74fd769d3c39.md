### Title
Partial Collateral Withdrawal Bypasses Minimum Deposit Invariant — (`core/contracts/Clearinghouse.sol`)

---

### Summary

`Clearinghouse.withdrawCollateral()` does not invoke `checkMinDeposit()` after reducing a subaccount's balance. A user who deposited above the enforced minimum can partially withdraw to leave a dust balance below that minimum, bypassing the invariant the protocol explicitly encodes.

---

### Finding Description

`Clearinghouse.sol` exposes `checkMinDeposit()` as an external validation helper used by the sequencer to gate deposits: [1](#0-0) 

The function accepts a caller-supplied `minDepositAmount`, converts the raw token amount to 18-decimal precision, prices it, and returns whether the deposit meets the threshold. This is the protocol's only on-chain expression of a minimum position size.

`withdrawCollateral()` performs no equivalent check after debiting the subaccount. Its sole post-state guard is an `INITIAL` health check: [2](#0-1) 

Specifically, lines 412–419 show the balance is decremented and only `getHealth(sender, INITIAL) >= 0` is asserted — `checkMinDeposit` is never consulted.

The withdrawal is reachable by any user via a signed `WithdrawCollateral` or `WithdrawCollateralV2` EIP-712 transaction routed through `EndpointTx.processTransactionImpl`: [3](#0-2) 

Both paths call `clearinghouse.withdrawCollateral(...)` with no minimum-balance guard.

---

### Impact Explanation

A user can:
1. Deposit collateral above the sequencer-enforced minimum (passes `checkMinDeposit`).
2. Submit a partial `WithdrawCollateral` for `amount - 1` (or any amount that keeps `INITIAL` health ≥ 0).
3. The remaining balance is below the minimum deposit threshold.

This creates dust subaccount positions the protocol explicitly intended to exclude. Such positions impose processing overhead during health checks, liquidations, and `claimSequencerFees` iterations over all product balances, and can be used to spam the system with economically negligible positions that are expensive to clear.

---

### Likelihood Explanation

The attack requires only a valid deposit followed by a standard signed withdrawal — both are normal user-facing operations with no privilege requirement. Any user who can deposit can execute this. The sequencer processes `WithdrawCollateral` transactions without any minimum-balance gate, so the bypass is unconditional once the initial deposit is accepted.

---

### Recommendation

After debiting the balance in `withdrawCollateral`, verify the remaining balance either equals zero (full withdrawal) or satisfies the minimum deposit threshold. Concretely, after `spotEngine.updateBalance(productId, sender, amountRealized)` at line 412, retrieve the updated balance and call `checkMinDeposit` with the remaining amount; revert if the result is `false` and the balance is non-zero. Alternatively, enforce that partial withdrawals are only permitted when the residual balance meets the minimum, mirroring the recommendation in the referenced report.

---

### Proof of Concept

1. Alice calls `Endpoint.depositCollateral(subaccountName, productId, 1000e6)`. The sequencer calls `checkMinDeposit(productId, 1000e6, minDepositAmount)` → `true`; deposit is processed.
2. Alice signs and submits a `WithdrawCollateral` transaction with `amount = 999e6`.
3. `EndpointTx.processTransactionImpl` routes to `clearinghouse.withdrawCollateral(alice, productId, 999e6, address(0), idx)`.
4. `withdrawCollateral` debits Alice's balance to `1e6`, calls `spotEngine.assertUtilization`, then checks `getHealth(alice, INITIAL) >= 0` — passes because `1e6` of collateral still provides positive health.
5. Alice's remaining balance of `1e6` is below `minDepositAmount`. `checkMinDeposit` was never called. The minimum deposit invariant is silently violated. [4](#0-3)

### Citations

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

**File:** core/contracts/Clearinghouse.sol (L698-715)
```text
    function checkMinDeposit(
        uint32 productId,
        uint128 amount,
        int256 minDepositAmount
    ) external returns (bool) {
        require(amount <= INT128_MAX, ERR_CONVERSION_OVERFLOW);
        uint8 decimals = _decimals(productId);
        require(decimals <= MAX_DECIMALS);

        int256 multiplier = int256(10**(MAX_DECIMALS - decimals));
        int128 amountRealized = int128(multiplier) * int128(amount);
        int128 priceX18 = ONE;
        if (productId != QUOTE_PRODUCT_ID) {
            priceX18 = _getPriceX18(productId);
        }

        return priceX18.mul(amountRealized) >= minDepositAmount;
    }
```

**File:** core/contracts/EndpointTx.sol (L413-436)
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
```
