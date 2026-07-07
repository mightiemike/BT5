### Title
Stale `priceX18` Used in Health Check During Slow-Mode Withdrawal Allows Undercollateralized Withdrawals - (`core/contracts/BaseEngine.sol`)

---

### Summary

`BaseEngine._calculateProductHealth()` computes subaccount health using `risk.priceX18`, a value stored in `RiskStore` that is only updated when the sequencer submits an `UpdatePrice` transaction. No timestamp is stored alongside the price, and no staleness check is performed before using it. During slow-mode withdrawals — the exact scenario where the sequencer is offline and prices stop being updated — a user can execute a withdrawal whose health check passes against an arbitrarily stale price, withdrawing collateral that would leave the account undercollateralized at current market prices.

---

### Finding Description

**Price storage with no timestamp:**

`RiskStore` in `RiskHelper.sol` stores `priceX18` as a plain `int128` with no associated update timestamp: [1](#0-0) 

**Price is only updated by the sequencer:**

`BaseEngine.updatePrice()` is the sole write path for `risk.priceX18`, and it is gated to the clearinghouse (which is only called from sequencer-submitted `UpdatePrice` transactions): [2](#0-1) 

In `EndpointTx.processTransactionImpl`, the `UpdatePrice` transaction type writes both `priceX18[productId]` in `EndpointStorage` and `_risk().value[productId].priceX18` in `BaseEngine`: [3](#0-2) 

**Health calculation uses the stale price without any freshness check:**

`_calculateProductHealth()` reads `risk.priceX18` directly and multiplies it against the position amount to compute health contribution: [4](#0-3) 

**Withdrawal health check relies on this stale price:**

`Clearinghouse.withdrawCollateral()` enforces a health check at the end using `getHealth()`, which internally calls `getHealthContribution()` → `_calculateProductHealth()` → `risk.priceX18`: [5](#0-4) 

**Slow-mode withdrawal is the reachable unprivileged entry path:**

Any user can submit a `WithdrawCollateral` slow-mode transaction via `submitSlowModeTransaction()`. After `SLOW_MODE_TX_DELAY` (3 days), any caller can execute it via `executeSlowModeTransaction()`: [6](#0-5) 

The slow-mode execution path calls `clearinghouse.withdrawCollateral()` directly: [7](#0-6) 

The slow-mode path is explicitly designed for sequencer downtime — the exact condition under which `UpdatePrice` transactions stop being submitted and `risk.priceX18` becomes stale.

---

### Impact Explanation

A user holding a leveraged position (e.g., borrowed spot assets or an open perp position) whose collateral has declined in value while the sequencer is offline can:

1. Submit a slow-mode `WithdrawCollateral` transaction.
2. Wait 3 days for the delay to expire.
3. Execute the withdrawal. The health check uses the stale (pre-decline) `risk.priceX18`, which overstates the collateral's value.
4. The `require(getHealth(...) >= 0)` check passes against the inflated stale price.
5. The user withdraws collateral that would leave the account undercollateralized at current market prices.

The corrupted state delta is: the protocol's on-chain collateral backing is reduced below the level required to cover outstanding liabilities at current prices, creating bad debt that is socialized across the insurance fund and remaining participants.

---

### Likelihood Explanation

The slow-mode mechanism is explicitly designed for sequencer downtime. Any period of sequencer unavailability lasting more than 3 days creates this window. A user who monitors sequencer liveness and has a leveraged position in a declining asset has a direct financial incentive to exploit this. No privileged access, leaked keys, or governance capture is required — only a call to `executeSlowModeTransaction()` after the delay expires.

---

### Recommendation

1. **Store a price update timestamp alongside `priceX18`** in `RiskStore` (or in a parallel mapping in `EndpointStorage`), updated every time `updatePrice()` is called.

2. **Add a staleness guard in `withdrawCollateral`** (or in `_calculateProductHealth`) that reverts if `block.timestamp - lastPriceUpdateTimestamp[productId] > MAX_PRICE_STALENESS`.

3. **Alternatively**, block slow-mode `WithdrawCollateral` execution when any product price in the subaccount's portfolio has not been updated within a configurable staleness window, forcing the sequencer to come back online and refresh prices before withdrawals can proceed.

---

### Proof of Concept

1. User deposits ETH collateral and opens a perp short on a non-ETH product, creating a cross-margin account with ETH as collateral.
2. Sequencer goes offline. `UpdatePrice` transactions stop. `risk.priceX18` for ETH is frozen at, say, `$3000`.
3. ETH market price drops to `$2000` over the next 3+ days.
4. User calls `submitSlowModeTransaction(WithdrawCollateral{productId: ETH, amount: X})`.
5. After 3 days, user calls `executeSlowModeTransaction()`.
6. `withdrawCollateral()` computes health using `risk.priceX18 = $3000` (stale), not `$2000` (current).
7. Health check passes. User withdraws `X` ETH.
8. At current prices, the account is now undercollateralized. The protocol holds bad debt.

### Citations

**File:** core/contracts/libraries/RiskHelper.sol (L14-24)
```text
    struct RiskStore {
        // these weights are all
        // between 0 and 2
        // these integers are the real
        // weights times 1e9
        int32 longWeightInitial;
        int32 shortWeightInitial;
        int32 longWeightMaintenance;
        int32 shortWeightMaintenance;
        int128 priceX18;
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

**File:** core/contracts/BaseEngine.sol (L273-276)
```text
    function updatePrice(uint32 productId, int128 priceX18) external virtual {
        require(msg.sender == address(_clearinghouse), ERR_UNAUTHORIZED);
        _risk().value[productId].priceX18 = priceX18;
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

**File:** core/contracts/Clearinghouse.sol (L415-420)
```text
        IProductEngine.HealthType healthType = sender == X_ACCOUNT
            ? IProductEngine.HealthType.PNL
            : IProductEngine.HealthType.INITIAL;

        require(getHealth(sender, healthType) >= 0, ERR_SUBACCT_HEALTH);
        emit ModifyCollateral(amountRealized, sender, productId);
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
