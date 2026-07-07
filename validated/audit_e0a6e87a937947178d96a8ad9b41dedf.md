### Title
`SpotEngineState.getBalance` Returns Understated Borrow Amount Due to Stale Interest Multiplier — (File: `core/contracts/SpotEngineState.sol`)

---

### Summary

`SpotEngineState.getBalance` computes a borrower's actual balance by multiplying `amountNormalized` against the last-persisted `cumulativeBorrowsMultiplierX18` from storage. Because `_updateState` is never called inside `getBalance`, the multiplier does not reflect interest that has accrued since the last `updateStates` call. Every downstream health check — including the one guarding `withdrawCollateral` — therefore operates on an understated borrow amount, producing an inflated health score. A borrower can exploit this window to withdraw collateral that should be locked.

---

### Finding Description

Nado's `SpotEngine` tracks balances in normalized form. The actual balance is:

```
actual_amount = amountNormalized × cumulativeMultiplierX18
```

Interest accrual advances `cumulativeBorrowsMultiplierX18` (and `cumulativeDepositsMultiplierX18`) inside `_updateState`, which is only called from `updateStates(dt)` — an `onlyEndpoint` function driven by the sequencer on a periodic schedule bounded by `dt < 7 * SECONDS_PER_DAY`.

`getBalance` in `SpotEngineState.sol` reads the state directly from storage without advancing the multiplier:

```solidity
// SpotEngineState.sol lines 246-254
function getBalance(uint32 productId, bytes32 subaccount)
    public view returns (Balance memory)
{
    State memory state = states[productId];                      // stale multipliers
    BalanceNormalized memory balance = balances[productId][subaccount];
    return balanceNormalizedToBalance(state, balance);           // uses stale state
}
```

`balanceNormalizedToBalance` selects the borrow multiplier for negative balances:

```solidity
// SpotEngineState.sol lines 180-192
function balanceNormalizedToBalance(State memory state, BalanceNormalized memory balance)
    internal pure returns (Balance memory)
{
    int128 cumulativeMultiplierX18;
    if (balance.amountNormalized > 0) {
        cumulativeMultiplierX18 = state.cumulativeDepositsMultiplierX18;
    } else {
        cumulativeMultiplierX18 = state.cumulativeBorrowsMultiplierX18; // stale
    }
    return Balance(balance.amountNormalized.mul(cumulativeMultiplierX18));
}
```

Because `cumulativeBorrowsMultiplierX18` in storage is the value from the **last** `updateStates` call, and the true current multiplier is `stored_multiplier × borrowRateMultiplierX18(elapsed_dt)`, the returned borrow amount is always understated during the interval between sequencer updates.

This stale balance propagates through the entire health-check call chain:

```
Clearinghouse.withdrawCollateral (line 419)
  → getHealth (line 71)
    → spotEngine.getHealthContribution (BaseEngine line 112)
      → _processBitmapChunk (line 137)
        → _calculateProductHealth (line 157)
          → _getBalance (SpotEngineState line 256)
            → getBalance (SpotEngineState line 246)   ← stale multiplier
```

The same stale path is used in `transferQuote` (line 249) and all liquidation health checks in `ClearinghouseLiq`.

---

### Impact Explanation

A borrower whose actual health (computed with the current multiplier) is negative can still pass the `require(getHealth(sender, healthType) >= 0)` check in `withdrawCollateral` because the stale multiplier understates their debt. They withdraw collateral that should be locked, leaving the protocol holding an undercollateralized position. The magnitude of the understatement grows with elapsed time since the last `updateStates` call and with the borrow rate. At the maximum allowed interval (7 days) and a non-trivial borrow rate, the gap is material enough to enable meaningful collateral extraction.

**Impact: 3** — Direct solvency/accounting corruption; a borrower can extract collateral the protocol believes is backing a debt that is actually larger than reported.

---

### Likelihood Explanation

The sequencer calls `updateStates` periodically. The gap between calls is a normal operating condition, not an edge case. Any borrower can observe the last update timestamp on-chain and call `withdrawCollateral` before the next `updateStates` is processed. No special privileges are required; the entry point is a standard user action.

**Likelihood: 4** — Reachable by any borrower at any time the sequencer has not updated within the current block; the window is always open between sequencer ticks.

---

### Recommendation

`getBalance` (and `getStateAndBalance`) should compute the current multiplier by applying the pending interest for the elapsed time before converting the normalized balance, analogous to how the external report's fix replaced a stale read with a function that first calls `accrueInterest`. Concretely, `getBalance` should call `_updateState(productId, state, elapsed_dt)` on the in-memory `state` before passing it to `balanceNormalizedToBalance`, where `elapsed_dt` is derived from the current block timestamp minus the last update timestamp. Alternatively, health-check paths should always be preceded by a state refresh, ensuring the multipliers used in `getHealthContribution` reflect accrued interest up to the current block.

---

### Proof of Concept

1. Deploy `SpotEngine`, `Clearinghouse`, `Endpoint` on a local fork.
2. Lender deposits 1,000,000 USDC quote collateral; borrower deposits 1,000 units of a spot asset as collateral.
3. Borrower borrows the maximum USDC allowed under initial health (QUOTE balance goes negative).
4. Sequencer calls `updateStates(dt)` at time T — multipliers are persisted.
5. Advance block time by 6 days (within the 7-day cap). Do **not** call `updateStates` again.
6. Call `spotEngine.getBalance(QUOTE_PRODUCT_ID, borrower)` — observe the returned borrow amount equals `amountNormalized × cumulativeBorrowsMultiplierX18_at_T` (stale, understated).
7. Compute the true borrow amount by manually applying `_updateState` with `dt = 6 days` — observe it is materially larger.
8. Call `Clearinghouse.withdrawCollateral` as the borrower for an amount that would make actual health negative. Observe the transaction succeeds because `getHealth` uses the stale balance from step 6.
9. Call `updateStates(6 days)` — now `getHealth` returns a negative value, confirming the account is undercollateralized after the withdrawal. [1](#0-0) [2](#0-1) [3](#0-2) [4](#0-3) [5](#0-4) [6](#0-5) [7](#0-6) [8](#0-7)

### Citations

**File:** core/contracts/SpotEngineState.sol (L52-56)
```text
    function _updateState(
        uint32 productId,
        State memory state,
        uint128 dt
    ) internal {
```

**File:** core/contracts/SpotEngineState.sol (L180-192)
```text
    function balanceNormalizedToBalance(
        State memory state,
        BalanceNormalized memory balance
    ) internal pure returns (Balance memory) {
        int128 cumulativeMultiplierX18;
        if (balance.amountNormalized > 0) {
            cumulativeMultiplierX18 = state.cumulativeDepositsMultiplierX18;
        } else {
            cumulativeMultiplierX18 = state.cumulativeBorrowsMultiplierX18;
        }

        return Balance(balance.amountNormalized.mul(cumulativeMultiplierX18));
    }
```

**File:** core/contracts/SpotEngineState.sol (L246-254)
```text
    function getBalance(uint32 productId, bytes32 subaccount)
        public
        view
        returns (Balance memory)
    {
        State memory state = states[productId];
        BalanceNormalized memory balance = balances[productId][subaccount];
        return balanceNormalizedToBalance(state, balance);
    }
```

**File:** core/contracts/SpotEngineState.sol (L265-283)
```text
    function updateStates(uint128 dt) external onlyEndpoint {
        State memory quoteState;
        require(dt < 7 * SECONDS_PER_DAY, ERR_INVALID_TIME);
        for (uint32 i = 0; i < productIds.length; i++) {
            uint32 productId = productIds[i];
            if (productId == NLP_PRODUCT_ID) {
                continue;
            }
            State memory state = states[productId];
            if (productId == QUOTE_PRODUCT_ID) {
                quoteState = state;
            }
            if (state.totalDepositsNormalized == 0) {
                continue;
            }
            _updateState(productId, state, dt);
            _setState(productId, state);
        }
    }
```

**File:** core/contracts/BaseEngine.sol (L112-135)
```text
    function getHealthContribution(
        bytes32 subaccount,
        IProductEngine.HealthType healthType
    ) public returns (int128 health) {
        uint32 maxBitmapIndex = _getMaxProductId() / 256;

        for (
            uint32 bitmapIndex = 0;
            bitmapIndex <= maxBitmapIndex;
            bitmapIndex++
        ) {
            uint256 bitmapChunk = _getBitmapChunk(subaccount, bitmapIndex);
            if (bitmapChunk == 0) {
                continue;
            }

            health += _processBitmapChunk(
                bitmapChunk,
                bitmapIndex,
                subaccount,
                healthType
            );
        }
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

**File:** core/contracts/Clearinghouse.sol (L71-84)
```text
    function getHealth(bytes32 subaccount, IProductEngine.HealthType healthType)
        public
        returns (int128 health)
    {
        ISpotEngine spotEngine = _spotEngine();
        IPerpEngine perpEngine = _perpEngine();

        health = spotEngine.getHealthContribution(subaccount, healthType);
        // min health means that it is attempting to borrow a spot that exists outside
        // of the risk system -- return min health to error out this action
        if (health == -INF) {
            return health;
        }
        health += perpEngine.getHealthContribution(subaccount, healthType);
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
