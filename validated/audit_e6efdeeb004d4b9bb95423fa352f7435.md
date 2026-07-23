Audit Report

## Title
`SwapAllowlistExtension` checks router address as `sender` instead of end user, enabling full allowlist bypass when router is allowlisted — (`metric-periphery/contracts/extensions/SwapAllowlistExtension.sol`)

## Summary

`SwapAllowlistExtension.beforeSwap` gates swaps by checking `allowedSwapper[pool][sender]`, where `sender` is the immediate caller of `pool.swap()`. When users route through `MetricOmmSimpleRouter`, `sender` is the router address, not the end user. The admin has no mechanism to grant router access to legitimate allowlisted users without also allowlisting the router globally — which allows any unpermissioned user to bypass the per-user restriction entirely by routing through the router.

## Finding Description

**Root cause — `MetricOmmPool.swap()` passes `msg.sender` as `sender`:**

`MetricOmmPool.swap()` calls `_beforeSwap(msg.sender, ...)`: [1](#0-0) 

`ExtensionCalling._beforeSwap` forwards `sender` unchanged to every configured extension: [2](#0-1) 

`SwapAllowlistExtension.beforeSwap` checks `allowedSwapper[msg.sender][sender]`, where `msg.sender` is the pool and `sender` is whoever called `pool.swap()`: [3](#0-2) 

**Router call path — all four entry points make the router the `sender`:**

`exactInputSingle` calls `pool.swap(params.recipient, ...)` directly, making the router `msg.sender` at the pool level: [4](#0-3) 

`exactInput` does the same for every hop: [5](#0-4) 

`exactOutputSingle` and `exactOutput` follow the same pattern: [6](#0-5) [7](#0-6) 

The router stores the originating user only in transient storage as the `payer` for the payment callback — it is never forwarded to the pool as an identity for access control purposes: [8](#0-7) 

**The broken invariant has two faces:**

1. **Broken functionality**: If the admin allowlists only individual user addresses (e.g., `userA`), those users cannot swap through the router because the router address is not allowlisted → `NotAllowedToSwap` reverts. The allowlist forces direct pool calls only, breaking normal UX.

2. **Full bypass**: To restore router access for allowlisted users, the admin must call `setAllowedToSwap(pool, router, true)`. This sets `allowedSwapper[pool][router] = true`, so the check passes for **any** caller who routes through the router — including non-allowlisted users. [9](#0-8) 

There is no separate "router allowlist" concept in the mapping — the admin is forced into the bypass to restore normal UX.

**Contrast with `DepositAllowlistExtension`** — that extension checks `owner` (the position owner), which is correctly preserved through the `MetricOmmPoolLiquidityAdder` path and is not subject to this flaw: [10](#0-9) 

## Impact Explanation

A pool deploying `SwapAllowlistExtension` to restrict swaps to a curated set of counterparties (KYC'd addresses, institutional partners, whitelisted market makers) loses that protection entirely once the router is allowlisted. Any unpermissioned user can call `MetricOmmSimpleRouter.exactInputSingle` and execute swaps against the pool, defeating the access control the pool admin configured. This constitutes broken core pool functionality and a direct admin-boundary break — the pool admin's configured restriction is bypassed by an unprivileged path.

## Likelihood Explanation

The trigger requires the pool admin to allowlist the router address. This is the natural and expected configuration step: allowlisted users need the router for multi-hop swaps, slippage protection, and deadline enforcement. The admin has no other way to grant router access to legitimate users without also opening the gate to everyone. The condition is therefore not hypothetical — it is the inevitable outcome of any operator trying to run a restricted pool with router support.

## Recommendation

The extension must check the **end user**, not the immediate pool caller. Two approaches:

1. **Preferred — trusted router forwarding**: The router stores the originating `msg.sender` in transient storage and exposes it via a known interface; the extension reads it when `sender` is a factory-registered router, using the transient value as the identity to check against `allowedSwapper`.

2. **Simpler — check `recipient` instead of `sender`**: For swap allowlists, gating the `recipient` (the address receiving output tokens) rather than the immediate caller may better reflect economic intent, though it shifts the check to a user-supplied field and requires careful analysis of multi-hop routing where intermediate recipients are the router itself.

## Proof of Concept

```
Setup:
  pool configured with SwapAllowlistExtension
  admin calls setAllowedToSwap(pool, userA, true)    // allowlist userA
  admin calls setAllowedToSwap(pool, router, true)   // allowlist router so userA can use it

Attack:
  userB (not allowlisted) calls:
    router.exactInputSingle({pool: pool, recipient: userB, ...})
      → pool.swap(userB, ...)  [msg.sender at pool = router]
        → _beforeSwap(sender=router, ...)
          → allowedSwapper[pool][router] == true  ✓
          → swap executes for userB

Result: userB successfully swaps against a pool configured to deny them.
```

The exact wrong value is the `sender` argument passed to `allowedSwapper[pool][sender]` — it resolves to the router address rather than the end user, causing the extension to authorize an identity (the router) that was never intended to represent a specific trader.

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

**File:** metric-periphery/contracts/extensions/SwapAllowlistExtension.sol (L17-19)
```text
  function setAllowedToSwap(address pool_, address swapper, bool allowed) external onlyPoolAdmin(pool_) {
    allowedSwapper[pool_][swapper] = allowed;
    emit AllowedToSwapSet(pool_, swapper, allowed);
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

**File:** metric-periphery/contracts/MetricOmmSimpleRouter.sol (L136-137)
```text
    (int128 amount0Delta, int128 amount1Delta) = IMetricOmmPoolActions(params.pool)
      .swap(params.recipient, params.zeroForOne, -expectedAmountOut, priceLimitX64, "", params.extensionData);
```

**File:** metric-periphery/contracts/MetricOmmSimpleRouter.sol (L165-181)
```text
    (int128 amount0Delta, int128 amount1Delta) = IMetricOmmPoolActions(pool)
      .swap(
        params.recipient,
        zeroForOne,
        -expectedAmountOut,
        MetricOmmSwapPath.openLimit(zeroForOne),
        abi.encode(
          ExactOutputIterateCallbackData({
          tokens: params.tokens,
          pools: params.pools,
          extensionDatas: params.extensionDatas,
          zeroForOneBitMap: params.zeroForOneBitMap,
          amountInMax: params.amountInMaximum
        })
        ),
        params.extensionDatas[tradesLeftAfterThis]
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
