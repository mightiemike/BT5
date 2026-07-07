### Title
Sequencer-Pushed `priceX18` Has No Staleness Validation, Enabling Stale-Price Exploitation via Slow Mode Withdrawal — (`core/contracts/Endpoint.sol`, `core/contracts/BaseEngine.sol`)

---

### Summary

Nado's on-chain price store (`priceX18`) is a push-type oracle exclusively updated by the off-chain sequencer. No staleness check exists anywhere in the price retrieval or health-check path. When the sequencer is offline, users can submit and self-execute `WithdrawCollateral` slow mode transactions after the 3-day delay using arbitrarily stale prices, allowing them to withdraw collateral that a current price would not permit.

---

### Finding Description

Prices in Nado are stored in two places, both updated only by the sequencer:

1. `EndpointStorage.priceX18[productId]` — set via `UpdatePrice` transactions dispatched by the sequencer.
2. `BaseEngine.risk[productId].priceX18` — set via `engine.updatePrice()`, called from `Clearinghouse.updatePrice()` which is `onlyEndpoint`.

The health check path used during withdrawals is:

```
clearinghouse.withdrawCollateral()
  → getHealth(sender, INITIAL)
    → spotEngine.getHealthContribution()
      → _calculateProductHealth()
        → risk.priceX18   ← no staleness check
```

`_calculateProductHealth` computes `amount.mul(weight).mul(risk.priceX18)` with no timestamp validation or freshness guard.

The slow mode path allows any user to submit a `WithdrawCollateral` transaction via `submitSlowModeTransaction()`, and after `SLOW_MODE_TX_DELAY` (3 days), anyone can call `executeSlowModeTransaction()` to process it. The sequencer is not required for execution after the delay.

```solidity
// Endpoint.sol
function executeSlowModeTransaction() external {
    SlowModeConfig memory _slowModeConfig = slowModeConfig;
    _executeSlowModeTransaction(_slowModeConfig, false);  // fromSequencer = false
    ...
}
```

```solidity
// EndpointTx.sol — processSlowModeTransactionImpl
} else if (txType == IEndpoint.TransactionType.WithdrawCollateral) {
    ...
    clearinghouse.withdrawCollateral(txn.sender, txn.productId, txn.amount, address(0), nSubmissions);
}
```

`withdrawCollateral` then calls `getHealth` using the stale `risk.priceX18` with no check on when it was last updated.

---

### Impact Explanation

If the sequencer goes offline for ≥3 days (the slow mode delay), prices stored on-chain become stale. A user holding a volatile collateral asset (e.g., ETH) whose real market price has dropped significantly can:

1. Submit a `WithdrawCollateral` slow mode transaction for the maximum amount their stale-price health allows.
2. Wait 3 days.
3. Call `executeSlowModeTransaction()` themselves.
4. The health check passes using the inflated stale price, allowing withdrawal of collateral that would fail under the current market price.

This is equivalent to the M-09 fallback oracle arbitrage: the sequencer-pushed price is a push-type oracle that is not updated during downtime, and the protocol has no mechanism to reject or penalize use of stale prices in the slow mode path.

The corrupted state delta is: `spotEngine.balance[sender][productId]` is decremented by more than the current-price health allows, leaving the account undercollateralized relative to real market prices.

---

### Likelihood Explanation

The sequencer is a centralized off-chain component. Any downtime event (infrastructure failure, upgrade, network partition) lasting ≥3 days triggers the window. The 3-day delay is hardcoded. A sophisticated user monitoring sequencer liveness can pre-submit the slow mode transaction and execute it the moment the delay expires, with no additional permissions required.

---

### Recommendation

1. **Staleness guard on slow mode withdrawals**: Record a `lastPriceUpdateTimestamp` per product when `UpdatePrice` is processed. In `withdrawCollateral` (or `getHealth`), revert if `block.timestamp - lastPriceUpdateTimestamp` exceeds a threshold (e.g., 1 hour).
2. **Disable slow mode withdrawals during price staleness**: If any product price used in the health check is stale, reject the slow mode withdrawal.
3. **Increase slow mode fee or add a price-freshness requirement**: Require that a valid price update was submitted within a recent window before a slow mode withdrawal can be executed.

---

### Proof of Concept

**Root cause — no staleness check in health computation:** [1](#0-0) 

`risk.priceX18` is used directly with no timestamp or freshness validation.

**Price is exclusively sequencer-pushed, no on-chain freshness metadata:** [2](#0-1) 

`updatePrice` is `onlyEndpoint`, meaning only the sequencer can update it. No timestamp is stored.

**Slow mode withdrawal is user-executable after 3 days without sequencer:** [3](#0-2) [4](#0-3) 

**Slow mode `WithdrawCollateral` calls `withdrawCollateral` which calls `getHealth` with stale price:** [5](#0-4) [6](#0-5) 

**`getHealth` delegates to engine health contributions using stale `risk.priceX18`:** [7](#0-6)

### Citations

**File:** core/contracts/BaseEngine.sol (L157-176)
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

**File:** core/contracts/Clearinghouse.sol (L358-375)
```text
    function updatePrice(bytes calldata transaction)
        external
        onlyEndpoint
        returns (uint32, int128)
    {
        IEndpoint.UpdatePrice memory txn = abi.decode(
            transaction[1:],
            (IEndpoint.UpdatePrice)
        );
        require(txn.priceX18 > 0, ERR_INVALID_PRICE);
        IProductEngine engine = productToEngine[txn.productId];
        if (address(engine) != address(0)) {
            engine.updatePrice(txn.productId, txn.priceX18);
            return (txn.productId, txn.priceX18);
        } else {
            return (0, 0);
        }
    }
```

**File:** core/contracts/Clearinghouse.sol (L415-420)
```text
        IProductEngine.HealthType healthType = sender == X_ACCOUNT
            ? IProductEngine.HealthType.PNL
            : IProductEngine.HealthType.INITIAL;

        require(getHealth(sender, healthType) >= 0, ERR_SUBACCT_HEALTH);
        emit ModifyCollateral(amountRealized, sender, productId);
```

**File:** core/contracts/Endpoint.sol (L196-199)
```text
        require(
            fromSequencer || (txn.executableAt <= block.timestamp),
            ERR_SLOW_TX_TOO_RECENT
        );
```

**File:** core/contracts/Endpoint.sol (L231-236)
```text
    function executeSlowModeTransaction() external {
        SlowModeConfig memory _slowModeConfig = slowModeConfig;
        _executeSlowModeTransaction(_slowModeConfig, false);
        nSubmissions += 1;
        slowModeConfig = _slowModeConfig;
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
