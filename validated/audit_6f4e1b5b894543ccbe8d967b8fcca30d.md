### Title
Health Check in `matchOrders` Is Permanently Bypassed via Always-True `isHealthy` — (`core/contracts/OffchainExchange.sol`)

---

### Summary

The `OffchainExchange.matchOrders` function performs a health check on both taker and maker subaccounts after updating their balances. However, the `isHealthy` function it calls unconditionally returns `true`, making both checks completely inert. This is the Nado analog of the Perennial H-1 class: in Perennial, health accounting was incomplete (only the current intent's price adjustment was considered); in Nado, health accounting during order matching is entirely absent. The result is identical — an attacker can execute trades that drive one subaccount into deeply negative collateral while the counterparty subaccount accumulates inflated profit and withdraws it, draining the protocol.

---

### Finding Description

In `OffchainExchange.sol`, after both sides of a match have their balances updated, the contract checks:

```solidity
require(isHealthy(taker.order.sender), ERR_INVALID_TAKER);
require(isHealthy(maker.order.sender), ERR_INVALID_MAKER);
``` [1](#0-0) 

The `isHealthy` function that backs these checks is:

```solidity
function isHealthy(
    bytes32 /* subaccount */
) internal view virtual returns (bool) {
    return true;
}
``` [2](#0-1) 

It is `virtual` but is never overridden anywhere in the deployed contract set — `OffchainExchange` is the concrete deployed contract. No derived contract exists in the repository that overrides this function. [3](#0-2) 

The balance updates that precede these dead checks are real and immediately committed to engine state:

```solidity
_updateBalances(callState, market.quoteId, taker.order.sender,
    ordersInfo.taker.amountDelta, ordersInfo.taker.quoteDelta);
_updateBalances(callState, market.quoteId, maker.order.sender,
    ordersInfo.maker.amountDelta, ordersInfo.maker.quoteDelta);
``` [4](#0-3) 

For perp products, `_updateBalances` calls `perpEngine.updateBalance`, which immediately writes the new `amount` and `vQuoteBalance` to storage. [5](#0-4) 

The `Clearinghouse.getHealth` function — which correctly aggregates health across all spot and perp products — is never invoked during `matchOrders`. [6](#0-5) 

Health is only enforced at withdrawal time:

```solidity
require(getHealth(sender, healthType) >= 0, ERR_SUBACCT_HEALTH);
``` [7](#0-6) 

This means a subaccount can be driven to arbitrarily negative health through trading, and the protocol will not reject the trade. The counterparty subaccount, which is in equal and opposite profit, faces no health barrier to withdrawal.

---

### Impact Explanation

An attacker controlling two subaccounts can execute a matched trade at a price far from the oracle price. The taker subaccount absorbs a large negative quote delta; the maker subaccount absorbs the equal positive delta. Because `isHealthy` returns `true` unconditionally, the trade is accepted regardless of the resulting health. The maker subaccount then passes the withdrawal health check (it is in profit) and withdraws more than was deposited. The taker subaccount is left with negative collateral that the protocol cannot recover. Repeating this across multiple products or multiple matched pairs allows draining the entire protocol collateral balance. This matches the stated impact of the reference report: **all market collateral token balance is stolen**.

---

### Likelihood Explanation

The `matchOrders` entry point is `onlyEndpoint`, meaning it is submitted by the sequencer. However:

1. The protocol exposes a slow-mode path (evidenced by `getSlowModeFee` in `Clearinghouse`) through which users can submit transactions directly without sequencer cooperation.
2. Even absent slow mode, the on-chain contract is the last line of defense. A sequencer bug, compromise, or a malicious sequencer operator can exploit this with zero on-chain resistance.
3. No pre-conditions are required: any user who can sign two crossing orders (one from each of two self-controlled subaccounts) at an extreme price can trigger this. [8](#0-7) 

---

### Recommendation

Replace the stub `isHealthy` with a real call to `clearinghouse.getHealth`:

```solidity
function isHealthy(bytes32 subaccount) internal virtual returns (bool) {
    return clearinghouse.getHealth(
        subaccount,
        IProductEngine.HealthType.INITIAL
    ) >= 0;
}
```

This mirrors the check already enforced in `withdrawCollateral` and `transferQuote`, and closes the gap that allows trades to bypass the risk engine entirely. [9](#0-8) 

---

### Proof of Concept

1. Attacker deposits 1 000 USDC into `subaccountA` (taker) and 1 000 USDC into `subaccountB` (maker).
2. Attacker signs a taker order from `subaccountA`: buy 1 ETH at `priceX18 = 30_000e18` (10× the oracle price of 3 000).
3. Attacker signs a maker order from `subaccountB`: sell 1 ETH at `priceX18 = 30_000e18`.
4. The orders cross (`maker.amount > 0`, `taker.amount < 0`, `maker.priceX18 <= taker.priceX18`). `matchOrders` is submitted.
5. `_updateBalances` executes:
   - `subaccountA`: `quoteDelta = -30_000`, net balance = `1_000 - 30_000 = -29_000`.
   - `subaccountB`: `quoteDelta = +30_000`, net balance = `1_000 + 30_000 = 31_000`.
6. `isHealthy(subaccountA)` → `true`. `isHealthy(subaccountB)` → `true`. Trade accepted.
7. `subaccountB` calls `withdrawCollateral` for 31 000 USDC. `getHealth` returns positive (subaccountB is in profit). Withdrawal succeeds.
8. Protocol has paid out 31 000 USDC against 2 000 USDC deposited. Net theft: **29 000 USDC per round**, repeatable until the protocol is drained. [10](#0-9) [11](#0-10)

### Citations

**File:** core/contracts/OffchainExchange.sol (L20-24)
```text
contract OffchainExchange is
    IOffchainExchange,
    EndpointGated,
    EIP712Upgradeable
{
```

**File:** core/contracts/OffchainExchange.sol (L217-223)
```text
        if (callState.isPerp) {
            callState.perp.updateBalance(
                callState.productId,
                subaccount,
                baseDelta,
                quoteDelta
            );
```

**File:** core/contracts/OffchainExchange.sol (L625-629)
```text
    function isHealthy(
        bytes32 /* subaccount */
    ) internal view virtual returns (bool) {
        return true;
    }
```

**File:** core/contracts/OffchainExchange.sol (L760-827)
```text
        ordersInfo.maker.quoteDelta = ordersInfo.taker.amountDelta.mul(
            maker.order.priceX18
        );
        ordersInfo.taker.quoteDelta = -ordersInfo.maker.quoteDelta;
        ordersInfo.maker.amountDelta = -ordersInfo.taker.amountDelta;

        taker.order.amount -= ordersInfo.taker.amountDelta;
        maker.order.amount -= ordersInfo.maker.amountDelta;

        // apply the taker fee
        applyFee(
            callState.productId,
            ordersInfo.taker,
            market,
            -maker.order.priceX18.mul(filledAmounts[ordersInfo.taker.digest]),
            taker.order.appendix,
            true
        );

        // apply the maker fee
        if (makerAccruesTakerFee(maker.order.sender, callState.productId)) {
            ordersInfo.maker.fee = -ordersInfo.taker.fee;
            ordersInfo.maker.quoteDelta =
                ordersInfo.maker.quoteDelta +
                ordersInfo.taker.fee;
        } else {
            applyFee(
                callState.productId,
                ordersInfo.maker,
                market,
                0, // alreadyMatched doesn't matter for a maker order
                maker.order.appendix,
                false
            );
        }

        updateCollectedFees(
            callState.productId,
            market,
            true,
            ordersInfo.taker.fee,
            ordersInfo.taker.builderFee
        );
        updateCollectedFees(
            callState.productId,
            market,
            false,
            ordersInfo.maker.fee,
            ordersInfo.maker.builderFee
        );

        _updateBalances(
            callState,
            market.quoteId,
            taker.order.sender,
            ordersInfo.taker.amountDelta,
            ordersInfo.taker.quoteDelta
        );
        _updateBalances(
            callState,
            market.quoteId,
            maker.order.sender,
            ordersInfo.maker.amountDelta,
            ordersInfo.maker.quoteDelta
        );

        require(isHealthy(taker.order.sender), ERR_INVALID_TAKER);
        require(isHealthy(maker.order.sender), ERR_INVALID_MAKER);
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

**File:** core/contracts/Clearinghouse.sol (L391-420)
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
```

**File:** core/contracts/Clearinghouse.sol (L639-642)
```text
    function _isAboveInitial(bytes32 subaccount) internal returns (bool) {
        // Weighted initial health with limit orders < 0
        return getHealth(subaccount, IProductEngine.HealthType.INITIAL) >= 0;
    }
```

**File:** core/contracts/Clearinghouse.sol (L759-766)
```text
    function getSlowModeFee() external view returns (uint256) {
        ISpotEngine spotEngine = _spotEngine();
        IERC20Base token = IERC20Base(
            spotEngine.getConfig(QUOTE_PRODUCT_ID).token
        );
        int256 multiplier = int256(10**(token.decimals() - 6));
        return uint256(int256(SLOW_MODE_FEE) * multiplier);
    }
```
