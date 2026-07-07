### Title
`withdrawCollateral` Checks Health Against Stale Sequencer Price Without Forcing a Price Sync — (`core/contracts/Clearinghouse.sol`)

---

### Summary

`Clearinghouse.withdrawCollateral` performs a post-withdrawal health check using `risk.priceX18`, which is the last price pushed by the sequencer via an `UpdatePrice` transaction. The function never forces a price sync before evaluating health. In the slow-mode (censorship-resistance) path, a user can force-process a withdrawal when the on-chain price is stale, causing the health check to pass on a position that is actually undercollateralized at the current market price.

---

### Finding Description

`Clearinghouse.withdrawCollateral` debits the subaccount balance and then checks health: [1](#0-0) 

The health check at line 419 calls `getHealth(sender, healthType)`, which delegates to `spotEngine.getHealthContribution` and `perpEngine.getHealthContribution`. Both ultimately reach `BaseEngine._calculateProductHealth`: [2](#0-1) 

At line 174, the health value is computed as `amount.mul(weight).mul(risk.priceX18)`. The `risk.priceX18` field is the last price written by the sequencer via `clearinghouse.updatePrice` → `engine.updatePrice`. There is no call to force-update this price inside `withdrawCollateral` before the health check is evaluated.

Prices are updated by the sequencer submitting `UpdatePrice` transactions, which flow through `EndpointTx` and call `engine.updatePrice`: [3](#0-2) 

The slow-mode path for `WithdrawCollateral` is: [4](#0-3) 

This path calls `clearinghouse.withdrawCollateral` directly with no preceding `UpdatePrice` step. If the sequencer has not pushed a recent price update (e.g., because it is censoring the user, which is exactly the scenario slow mode is designed to handle), the health check runs against a stale `risk.priceX18`.

---

### Impact Explanation

A user holding a perp position that has become undercollateralized due to adverse price movement can force-process a slow-mode `WithdrawCollateral` transaction while the on-chain `risk.priceX18` still reflects the old (favorable) price. The health check at line 419 passes because it uses the stale price, and the user successfully withdraws collateral from a position that is actually below the initial margin requirement. This directly corrupts the solvency accounting of the protocol: the subaccount's real health is negative, but the on-chain check reports it as non-negative.

---

### Likelihood Explanation

The condition requires two simultaneous factors: (1) the sequencer has not pushed a recent `UpdatePrice` transaction (either due to delay or active censorship of the user), and (2) the market price has moved adversely enough to make the position undercollateralized. Slow mode exists precisely for the censorship scenario, making this a realistic and reachable path for an unprivileged user. No admin access, key compromise, or governance capture is required.

---

### Recommendation

Before evaluating the health check in `withdrawCollateral`, force-apply the latest price state for all products relevant to the subaccount. Concretely, add a call equivalent to `engine.updatePrice(productId, latestPrice)` (or a batch equivalent) before the `getHealth` call at line 419, analogous to the Perennial mitigation of calling `settleForAccount(msg.sender)` before checking margin requirements.

---

### Proof of Concept

1. User opens a large perp long position and deposits collateral just above the initial margin threshold.
2. Market price drops 10%; the position is now undercollateralized at the real price.
3. The sequencer has not yet submitted an `UpdatePrice` transaction reflecting the new price (or is censoring the user).
4. User submits a `WithdrawCollateral` slow-mode transaction via `Endpoint`.
5. After the slow-mode timeout, the user (or anyone) calls the slow-mode force-process path, which invokes `EndpointTx.processSlowModeTransactionImpl` → `clearinghouse.withdrawCollateral`.
6. Inside `withdrawCollateral`, `getHealth` is called. `BaseEngine._calculateProductHealth` computes health using the stale `risk.priceX18` (the pre-drop price), so health appears non-negative.
7. The `require(getHealth(sender, healthType) >= 0, ERR_SUBACCT_HEALTH)` check passes.
8. Collateral is transferred out. The subaccount is now undercollateralized at the true market price, socializing the loss to the insurance fund or other participants.

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

**File:** core/contracts/BaseEngine.sol (L157-177)
```text
    function _calculateProductHealth(
        uint32 productId,
        bytes32 subaccount,
        IProductEngine.HealthType healthType
    ) internal returns (int128 health) {
        RiskHelper.Risk memory risk = _risk(productId);
        (int128 amount, int128 quoteAmount) = _getBalance(
            productId,
            subaccount
        );
        int128 weight = RiskHelper._getWeightX18(risk, amount, healthType);
        health += quoteAmount;

        if (amount != 0) {
            if (weight == 2 * ONE) {
                return -INF;
            }
            health += amount.mul(weight).mul(risk.priceX18);
            emit PriceQuery(productId);
        }
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

**File:** core/contracts/EndpointTx.sol (L486-492)
```text
        } else if (txType == IEndpoint.TransactionType.UpdatePrice) {
            (uint32 productId, int128 newPriceX18) = clearinghouse.updatePrice(
                transaction
            );
            if (productId != 0) {
                priceX18[productId] = newPriceX18;
            }
```
