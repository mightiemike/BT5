I have read the core pool, factory, and extension-calling contracts. Let me now synthesize the findings.

**Key code paths examined:**
- `MetricOmmPoolFactory.createPool` — stores `priceProviderTimelock` with no minimum enforcement
- `proposePoolPriceProvider` / `executePoolPriceProviderUpdate` — the two-step price-provider change flow
- `MetricOmmPool.swap` — fetches prices exclusively from the configured price provider
- `ExtensionCalling._beforeSwap` / `_afterSwap` — hook dispatch
- `simulateSwapAndRevert` — passes caller-supplied bid/ask to extension hooks (but always reverts, so no persistent state change)

---

### Title
Zero-Timelock Price Provider Change Enables Pool Admin to Drain All LP Funds — (File: `metric-core/contracts/MetricOmmPoolFactory.sol`)

### Summary

`MetricOmmPoolFactory.createPool` stores `params.priceProviderTimelock` with no minimum value check. When set to `0`, the pool admin can propose and execute a malicious price provider replacement in the same block, bypassing the intended timelock guard entirely and draining all LP funds through manipulated swap prices.

### Finding Description

In `createPool`, the timelock is stored verbatim:

```solidity
priceProviderTimelock[pool] = params.priceProviderTimelock;
``` [1](#0-0) 

When `priceProviderTimelock = 0`, `proposePoolPriceProvider` computes:

```solidity
uint256 executeAfter = block.timestamp + timelock; // = block.timestamp + 0
``` [2](#0-1) 

`executePoolPriceProviderUpdate` then checks:

```solidity
if (block.timestamp < execAfter) revert PriceProviderTimelockNotElapsed(...);
``` [3](#0-2) 

Because `execAfter == block.timestamp`, the condition `block.timestamp < block.timestamp` is `false`, so the update executes immediately. Both calls can land in the same block, making the effective timelock **zero seconds**.

The only validation applied to the replacement provider is:

```solidity
if (IPriceProvider(priceProvider).token0() != token0 || IPriceProvider(priceProvider).token1() != token1) {
    revert PriceProviderTokenMismatch();
}
``` [4](#0-3) 

A malicious provider trivially satisfies this by returning the correct token addresses while returning arbitrarily manipulated bid/ask prices. The `swap` function then fetches prices exclusively from this provider:

```solidity
(uint128 bidPriceX64, uint128 askPriceX64) = _getBidAndAskPriceX64();
``` [5](#0-4) 

with no further sanity bounds beyond `bid < ask` and `bid != 0`:

```solidity
if (bid >= ask) revert BidGreaterThanAsk();
if (bid == 0) revert BidIsZero();
``` [6](#0-5) 

A malicious provider returning `bid = 1, ask = 2` (in Q64.64 units) against a pool whose fair price is orders of magnitude higher causes every swap to execute at a price that transfers LP assets to the swapper at near-zero cost.

### Impact Explanation

**Critical — direct loss of all LP principal.** Once the malicious price provider is active, any swap drains the pool at the attacker-controlled price. Because `binTotals` tracks the full pool balance and `swap` transfers tokens out before the callback check, the entire pool can be emptied in one or a few swaps. No extension guard can compensate: the `beforeSwap` hook receives the manipulated bid/ask values and the swap math proceeds from them.

### Likelihood Explanation

**Medium.** The factory imposes no minimum timelock and emits no warning. A pool admin who creates a pool with `priceProviderTimelock = 0` retains the ability to drain it at any time after LPs deposit. LPs have no on-chain mechanism to enforce a minimum timelock; they must inspect the factory state manually. The attack requires only two factory transactions in the same block, executable atomically via a multicall wrapper.

### Recommendation

Enforce a protocol-level minimum timelock in `createPool` and reject values below it (except `type(uint256).max` for the immutable-provider path):

```solidity
uint256 constant MIN_PRICE_PROVIDER_TIMELOCK = 48 hours;

// in _validatePoolParameters or createPool:
if (
    params.priceProviderTimelock != type(uint256).max &&
    params.priceProviderTimelock < MIN_PRICE_PROVIDER_TIMELOCK
) revert TimelockTooShort();
```

Expose `priceProviderTimelock` prominently in the `PoolCreated` event (it is already emitted) and document that LPs must verify it before depositing.

### Proof of Concept

1. Pool admin calls `createPool` with `params.priceProviderTimelock = 0`. Factory stores `priceProviderTimelock[pool] = 0`.
2. LPs call `addLiquidity` and deposit token0 and token1.
3. Pool admin deploys `MaliciousPriceProvider` implementing `token0()` → pool's token0, `token1()` → pool's token1, `getBidAndAskPrice()` → `(1, 2)` (Q64.64, effectively price ≈ 0).
4. Pool admin calls `proposePoolPriceProvider(pool, maliciousPriceProvider)`. `executeAfter = block.timestamp`.
5. Pool admin calls `executePoolPriceProviderUpdate(pool)` in the same block. Check `block.timestamp < block.timestamp` is false; price provider is updated.
6. Pool admin calls `swap(pool, false /*oneForZero*/, largeAmount, 0, ...)`. `_getBidAndAskPriceX64()` returns `(1, 2)`. Swap math prices token0 at near-zero cost in token1. Pool admin receives all token0 for negligible token1.
7. Repeat for token1 direction. All LP funds drained.

### Citations

**File:** metric-core/contracts/MetricOmmPoolFactory.sol (L213-213)
```text
    priceProviderTimelock[pool] = params.priceProviderTimelock;
```

**File:** metric-core/contracts/MetricOmmPoolFactory.sol (L487-490)
```text
    uint256 executeAfter = block.timestamp + timelock;
    pendingPriceProvider[pool] = newPriceProvider;
    pendingPriceProviderExecuteAfter[pool] = executeAfter;
    emit PoolPriceProviderChangeProposed(pool, current, newPriceProvider, executeAfter);
```

**File:** metric-core/contracts/MetricOmmPoolFactory.sol (L498-499)
```text
    // forge-lint: disable-next-line(block-timestamp) -- timelock enforcement legitimately relies on `block.timestamp`.
    if (block.timestamp < execAfter) revert PriceProviderTimelockNotElapsed(execAfter, block.timestamp);
```

**File:** metric-core/contracts/MetricOmmPoolFactory.sol (L543-545)
```text
    if (IPriceProvider(priceProvider).token0() != token0 || IPriceProvider(priceProvider).token1() != token1) {
      revert PriceProviderTokenMismatch();
    }
```

**File:** metric-core/contracts/MetricOmmPool.sol (L228-228)
```text
    (uint128 bidPriceX64, uint128 askPriceX64) = _getBidAndAskPriceX64();
```

**File:** metric-core/contracts/MetricOmmPool.sol (L807-809)
```text
      if (bid >= ask) revert BidGreaterThanAsk();
      if (bid == 0) revert BidIsZero();
      return (bid, ask);
```
