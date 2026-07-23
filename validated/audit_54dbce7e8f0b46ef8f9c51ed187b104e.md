Audit Report

## Title
SwapAllowlistExtension Gates the Router Address Instead of the Real User, Allowing Any Caller to Bypass a Curated Pool's Swap Allowlist — (`metric-periphery/contracts/extensions/SwapAllowlistExtension.sol`)

## Summary

`SwapAllowlistExtension.beforeSwap` checks the `sender` argument, which `MetricOmmPool.swap` sets to `msg.sender` of the `pool.swap()` call. When a user routes through `MetricOmmSimpleRouter`, `msg.sender` of `pool.swap()` is the router contract, not the end user. If the pool admin allowlists the router so that permitted users can trade through it, every non-allowlisted user can also bypass the guard by routing through the same public router.

## Finding Description

`MetricOmmPool.swap` passes `msg.sender` as the `sender` argument to `_beforeSwap`: [1](#0-0) 

`ExtensionCalling._beforeSwap` forwards that value unchanged to every configured extension: [2](#0-1) 

`SwapAllowlistExtension.beforeSwap` then checks `allowedSwapper[msg.sender][sender]`, where `msg.sender` is the pool and `sender` is whatever called `pool.swap()`: [3](#0-2) 

When a user calls `MetricOmmSimpleRouter.exactInputSingle`, the router calls `pool.swap(params.recipient, ...)` directly — making the router the `msg.sender` of `pool.swap()`: [4](#0-3) 

The same applies to `exactInput`, `exactOutputSingle`, and `exactOutput`: [5](#0-4) 

This creates an inescapable dilemma for pool admins:

| Admin action | Effect |
|---|---|
| Do **not** allowlist the router | Allowlisted users cannot trade through the router at all |
| Allowlist the router | Every non-allowlisted user can bypass the guard via the router |

By contrast, `DepositAllowlistExtension.beforeAddLiquidity` correctly gates by `owner` (the position beneficiary), not `sender` (the operator), avoiding this class of bug: [6](#0-5) 

## Impact Explanation

A curated pool deploying `SwapAllowlistExtension` to restrict trading to a known set of addresses (e.g., KYC'd counterparties, whitelisted market makers) is fully bypassed for any user who routes through `MetricOmmSimpleRouter`. The attacker receives pool output tokens and pays input tokens exactly as a normal swap — there is no slippage or price difference from the bypass. The pool's LP funds are exposed to unrestricted trading against the oracle price, which is the exact risk the allowlist was deployed to prevent. This is a direct loss-of-policy impact with fund-level consequences (LP assets traded against unintended counterparties). This matches the "Admin-boundary break" and "Broken core pool functionality causing loss of funds" allowed impact categories, and the Smart Audit Pivot explicitly flags: "Allowlist path: deposit/swap allowlist checks must cover the exact actor/action intended and cannot be bypassed through router."

## Likelihood Explanation

- `MetricOmmSimpleRouter` is the canonical public swap entrypoint documented in the periphery.
- Any user who reads the router interface can call `exactInputSingle` with a curated pool address.
- No privileged access, no special token, no admin cooperation is required.
- The bypass is reachable in a single transaction.
- Pool admins have no on-chain remedy short of replacing the extension contract.

## Recommendation

`SwapAllowlistExtension.beforeSwap` must check the economically relevant actor — the end user — not the intermediary. Two complementary fixes:

1. **Pass the original `msg.sender` through the router.** The router should encode the real user address as a dedicated field (e.g., `originator`) in `extensionData` before calling `pool.swap()`, and the extension should decode and check it. The `onlyPool` modifier on the extension ensures only the pool can invoke the hook, preventing spoofing.

2. **Mirror the deposit allowlist pattern.** Gate on `recipient` (the swap output beneficiary) instead of `sender`. This is the analogous "beneficiary" for swaps and is already passed through the call chain unchanged.

## Proof of Concept

```
Setup:
  - Deploy pool with SwapAllowlistExtension configured.
  - Pool admin calls setAllowedToSwap(pool, alice, true).
  - Pool admin calls setAllowedToSwap(pool, router, true)
    (required so alice can use the router).

Attack (bob, not allowlisted):
  1. bob calls MetricOmmSimpleRouter.exactInputSingle({
       pool: curated_pool,
       recipient: bob,
       ...
     })
  2. Router calls pool.swap(bob, ...) — msg.sender of pool.swap = router.
  3. Pool calls _beforeSwap(sender=router, ...).
  4. Extension checks allowedSwapper[pool][router] → true.
  5. Swap executes. Bob receives output tokens.

Result: bob, who is not on the allowlist, successfully swaps against the
curated pool. The allowlist is completely bypassed.
```

### Citations

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

**File:** metric-core/contracts/ExtensionCalling.sol (L149-177)
```text
  function _beforeSwap(
    address sender,
    address recipient,
    bool zeroForOne,
    int128 amountSpecified,
    uint128 priceLimitX64,
    uint256 packedSlot0Initial,
    uint128 bidPriceX64,
    uint128 askPriceX64,
    bytes calldata extensionData
  ) internal {
    _callExtensionsInOrder(
      BEFORE_SWAP_ORDER,
      abi.encodeCall(
        IMetricOmmExtensions.beforeSwap,
        (
          sender,
          recipient,
          zeroForOne,
          amountSpecified,
          priceLimitX64,
          packedSlot0Initial,
          bidPriceX64,
          askPriceX64,
          extensionData
        )
      )
    );
  }
```

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

**File:** metric-periphery/contracts/extensions/DepositAllowlistExtension.sol (L32-42)
```text
  function beforeAddLiquidity(address, address owner, uint80, LiquidityDelta calldata, bytes calldata)
    external
    view
    override
    returns (bytes4)
  {
    if (!allowAllDepositors[msg.sender] && !allowedDepositor[msg.sender][owner]) {
      revert IMetricOmmPoolActions.NotAllowedToDeposit();
    }
    return IMetricOmmExtensions.beforeAddLiquidity.selector;
  }
```
