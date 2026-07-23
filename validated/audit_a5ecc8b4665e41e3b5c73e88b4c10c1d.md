Audit Report

## Title
`SwapAllowlistExtension.beforeSwap` checks router address instead of originating user, allowing allowlist bypass via `MetricOmmSimpleRouter` — (`metric-periphery/contracts/extensions/SwapAllowlistExtension.sol`)

## Summary
`SwapAllowlistExtension.beforeSwap` gates on the `sender` argument, which the pool sets to `msg.sender` of `pool.swap()`. When any user routes through `MetricOmmSimpleRouter`, `msg.sender` of `pool.swap()` is the router contract, not the user. If the pool admin allowlists the router (required for any legitimate user to trade through it), every non-allowlisted address can bypass the guard by calling the same public router.

## Finding Description
In `MetricOmmPool.sol`, the swap function passes `msg.sender` as the first argument to `_beforeSwap`:

```solidity
// MetricOmmPool.sol L230-231
_beforeSwap(
  msg.sender,   // becomes `sender` in the hook
  recipient, ...
```

`SwapAllowlistExtension.beforeSwap` then checks:

```solidity
// SwapAllowlistExtension.sol L37
if (!allowAllSwappers[msg.sender] && !allowedSwapper[msg.sender][sender]) {
```

Here `msg.sender` is the pool (the caller of the extension) and `sender` is the argument forwarded from the pool — i.e., the direct caller of `pool.swap()`. When a user calls `MetricOmmSimpleRouter.exactInputSingle`, the router calls `pool.swap()` directly:

```solidity
// MetricOmmSimpleRouter.sol L72-80
(int128 amount0Delta, int128 amount1Delta) = IMetricOmmPoolActions(params.pool)
  .swap(params.recipient, params.zeroForOne, ...);
```

So `sender` in the hook equals the router address. The check becomes `allowedSwapper[pool][router]`. If the pool admin has allowlisted the router (which is a prerequisite for any allowlisted user to trade through it), this check passes for **every** caller of the router, regardless of whether they are individually allowlisted. The same flaw applies to `exactInput`, `exactOutputSingle`, and `exactOutput`. The `DepositAllowlistExtension` does not share this flaw because it gates on `owner` (the position owner), not the adder's address.

## Impact Explanation
A pool configured with `SwapAllowlistExtension` to restrict trading to a curated set of counterparties (e.g., a KYC-gated or institutional pool) can be freely traded against by any public user via the router. This breaks the core pool functionality the extension was deployed to enforce. If the pool's pricing or liquidity strategy depends on only trusted counterparties executing swaps, unrestricted access can cause direct loss of LP principal — matching the "Broken core pool functionality causing loss of funds" and "Admin-boundary break" allowed impacts.

## Likelihood Explanation
High. `MetricOmmSimpleRouter` is a public, permissionless contract. The only precondition is that the pool admin has allowlisted the router, which is a necessary operational step for any legitimate allowlisted user to trade through the router. No privileged access, special token, or malicious setup is required. Any address can call `exactInputSingle` targeting the pool.

## Recommendation
The `beforeSwap` hook must gate on the economic actor, not the immediate caller of `pool.swap()`. The preferred fix is to have the router encode the originating `msg.sender` into `extensionData` and have the extension verify the router's identity (e.g., via a factory-registered router registry) before trusting that field. Alternatively, document and enforce that pools using `SwapAllowlistExtension` must not allowlist any router contract, and users must call `pool.swap()` directly — but this eliminates router usability for allowlisted pools.

## Proof of Concept
1. Deploy pool with `SwapAllowlistExtension` in `BEFORE_SWAP_ORDER`.
2. Pool admin calls `setAllowedToSwap(pool, Alice, true)` — only Alice should swap.
3. Alice wants to use the router; admin calls `setAllowedToSwap(pool, router, true)`.
4. Bob (not allowlisted) calls `MetricOmmSimpleRouter.exactInputSingle({pool: pool, recipient: Bob, ...})`.
5. Router calls `pool.swap(Bob, ...)` with `msg.sender = router`.
6. Pool calls `_beforeSwap(router, Bob, ...)` — `sender = router`.
7. `SwapAllowlistExtension.beforeSwap` evaluates `allowedSwapper[pool][router]` → `true` → hook passes.
8. Bob's swap executes despite not being on the allowlist. [1](#0-0) [2](#0-1) [3](#0-2) [4](#0-3)

### Citations

**File:** metric-periphery/contracts/extensions/SwapAllowlistExtension.sol (L31-41)
```text
  function beforeSwap(address sender, address, bool, int128, uint128, uint256, uint128, uint128, bytes calldata)
    external
    view
    override
    returns (bytes4)
  {
    if (!allowAllSwappers[msg.sender] && !allowedSwapper[msg.sender][sender]) {
      revert IMetricOmmPoolActions.NotAllowedToSwap();
    }
    return IMetricOmmExtensions.beforeSwap.selector;
  }
```

**File:** metric-core/contracts/MetricOmmPool.sol (L230-240)
```text
    _beforeSwap(
      msg.sender,
      recipient,
      zeroForOne,
      amountSpecified,
      priceLimitX64,
      packedSlot0Initial,
      bidPriceX64,
      askPriceX64,
      extensionData
    );
```

**File:** metric-periphery/contracts/MetricOmmSimpleRouter.sol (L71-80)
```text
    _setNextCallbackContext(params.pool, CALLBACK_MODE_JUST_PAY, msg.sender, params.tokenIn);
    (int128 amount0Delta, int128 amount1Delta) = IMetricOmmPoolActions(params.pool)
      .swap(
        params.recipient,
        params.zeroForOne,
        MetricOmmSwapInputs.asAmountSpecifiedIn(params.amountIn),
        priceLimitX64,
        "",
        params.extensionData
      );
```

**File:** metric-periphery/contracts/MetricOmmSimpleRouter.sol (L103-112)
```text
      _setNextCallbackContext(pool, CALLBACK_MODE_JUST_PAY, i == 0 ? msg.sender : address(this), params.tokens[i]);
      (int128 amount0Delta, int128 amount1Delta) = IMetricOmmPoolActions(pool)
        .swap(
          i == last ? params.recipient : address(this),
          zeroForOne,
          amount,
          MetricOmmSwapPath.openLimit(zeroForOne),
          "",
          params.extensionDatas[i]
        );
```
