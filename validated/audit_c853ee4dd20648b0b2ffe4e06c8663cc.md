Audit Report

## Title
SwapAllowlistExtension Allowlist Bypass via Router: Any User Can Swap on Allowlisted Pools Through MetricOmmSimpleRouter — (`metric-periphery/contracts/extensions/SwapAllowlistExtension.sol`)

## Summary

`SwapAllowlistExtension.beforeSwap` gates swaps by checking `allowedSwapper[pool][sender]`, where `sender` is the immediate caller of `pool.swap()`. When users route through `MetricOmmSimpleRouter`, `sender` is the router address, not the actual user. If the pool admin allowlists the router (the only way to let allowlisted users use the router), any unprivileged user can bypass the allowlist entirely by routing through the router, breaking the pool admin's intended access control.

## Finding Description

**Root cause — identity mismatch in the allowlist check:**

`SwapAllowlistExtension.beforeSwap` receives `sender` as its first argument and checks it against the per-pool allowlist:

```solidity
// metric-periphery/contracts/extensions/SwapAllowlistExtension.sol L37
if (!allowAllSwappers[msg.sender] && !allowedSwapper[msg.sender][sender]) {
    revert IMetricOmmPoolActions.NotAllowedToSwap();
}
```

`msg.sender` here is the pool (correct). `sender` is whatever the pool passed as the first argument to the hook. [1](#0-0) 

**The pool always passes `msg.sender` of its own `swap()` call as `sender`:**

```solidity
// metric-core/contracts/MetricOmmPool.sol L230-240
_beforeSwap(
    msg.sender,  // ← immediate caller of pool.swap(), not the end user
    recipient,
    ...
);
``` [2](#0-1) 

**The router is the immediate caller of `pool.swap()`:**

`MetricOmmSimpleRouter.exactInputSingle` calls `IMetricOmmPoolActions(params.pool).swap(...)` directly. The pool's `msg.sender` is therefore the **router contract**, not the end user: [3](#0-2) 

The same applies to `exactInput` (multi-hop) and `exactOutputSingle`: [4](#0-3) [5](#0-4) 

**The dilemma the pool admin faces:**

| Admin choice | Effect |
|---|---|
| Router NOT allowlisted | Allowlisted users cannot use the router at all — broken UX |
| Router IS allowlisted | `allowedSwapper[pool][router]` is `true`, so **every** user who calls the router passes the check — full bypass |

There is no configuration that allows specific users to use the router while blocking others. The extension has no mechanism to inspect the actual end-user identity when an intermediary is involved. The `extensionData` field passed through the router is caller-controlled and not authenticated, so it cannot be trusted as a source of user identity. [6](#0-5) 

## Impact Explanation

The `SwapAllowlistExtension` is the production guard for restricting swap access on a pool. Once the router is allowlisted (the only practical choice for a pool that wants allowlisted users to use the standard periphery), the guard is completely inert for router-mediated swaps. Any unprivileged address can call `exactInputSingle` / `exactInput` / `exactOutputSingle` on the router and trade against the pool's liquidity without appearing on the allowlist. This is an admin-boundary break: the pool admin's intended access control is bypassed by an unprivileged path through a public periphery contract. [7](#0-6) 

## Likelihood Explanation

High. The router is the standard, publicly documented entry point for swaps. Pool admins who deploy a `SwapAllowlistExtension` pool and want their allowlisted users to have a normal UX will allowlist the router. The bypass is then reachable by any address with no special privileges, no flash loan, and no malicious setup — a single call to `exactInputSingle` suffices. [3](#0-2) 

## Recommendation

The extension must check the **actual end-user identity**, not the immediate caller. Two viable approaches:

1. **Pass the real user in `extensionData`**: The router encodes `msg.sender` into `extensionData`; the extension decodes and verifies it, then cross-checks that `msg.sender` (the pool) confirms the router is a trusted forwarder registered in a factory-controlled registry.
2. **Check `sender` for direct calls, decode user for router calls**: The extension inspects whether `sender` is a known trusted forwarder and, if so, reads the real user from a standardised field in `extensionData`.

Either way, the allowlist lookup must resolve to the economic actor (the address paying tokens and receiving output), not the contract that happened to call `pool.swap()`. [1](#0-0) 

## Proof of Concept

```
Setup
─────
1. Pool admin deploys pool with SwapAllowlistExtension (extension1 = allowlist, beforeSwap order = 1).
2. Admin allowlists alice:  extension.setAllowedToSwap(pool, alice, true)
3. Admin allowlists router: extension.setAllowedToSwap(pool, router, true)
   (required so alice can use the router)

Attack
──────
4. eve (not allowlisted) calls:
       router.exactInputSingle({pool: pool, recipient: eve, ...})

5. Router calls pool.swap(eve, zeroForOne, amount, limit, "", "")
   → pool.msg.sender = router

6. Pool calls _beforeSwap(sender=router, ...)
   → extension.beforeSwap(sender=router, ...)
   → checks allowedSwapper[pool][router] == true  ✓ passes

7. Swap executes. Eve receives output tokens.
   SwapAllowlistExtension never saw eve's address.

Result: eve, who is not on the allowlist, successfully swaps on a pool
        that is supposed to be restricted to allowlisted users only.
``` [8](#0-7) [9](#0-8) [3](#0-2)

### Citations

**File:** metric-periphery/contracts/extensions/SwapAllowlistExtension.sol (L11-41)
```text
contract SwapAllowlistExtension is BaseMetricExtension, ISwapAllowlistExtension {
  mapping(address pool => mapping(address swapper => bool)) public allowedSwapper;
  mapping(address pool => bool) public allowAllSwappers;

  constructor(address factory_) BaseMetricExtension(factory_) {}

  function setAllowedToSwap(address pool_, address swapper, bool allowed) external onlyPoolAdmin(pool_) {
    allowedSwapper[pool_][swapper] = allowed;
    emit AllowedToSwapSet(pool_, swapper, allowed);
  }

  function setAllowAllSwappers(address pool_, bool allowed) external onlyPoolAdmin(pool_) {
    allowAllSwappers[pool_] = allowed;
    emit AllowAllSwappersSet(pool_, allowed);
  }

  function isAllowedToSwap(address pool_, address swapper) external view returns (bool) {
    return allowAllSwappers[pool_] || allowedSwapper[pool_][swapper];
  }

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

**File:** metric-core/contracts/MetricOmmPool.sol (L224-241)
```text
  ) external whenNotPaused nonReentrant(PoolActions.SWAP) returns (int128, int128) {
    require(amountSpecified != 0, InvalidAmount());

    uint256 packedSlot0Initial = Slot0Library.loadPackedSlot0();
    (uint128 bidPriceX64, uint128 askPriceX64) = _getBidAndAskPriceX64();

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

**File:** metric-periphery/contracts/MetricOmmSimpleRouter.sol (L67-86)
```text
  function exactInputSingle(ExactInputSingleParams calldata params) external payable returns (uint256 amountOut) {
    _checkDeadline(params.deadline);
    uint128 priceLimitX64 = MetricOmmSwapPath.normalizePriceLimit(params.zeroForOne, params.priceLimitX64);

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
    int128 out = MetricOmmSwapResults.extractAmountOut(params.zeroForOne, amount0Delta, amount1Delta);
    amountOut = MetricOmmSwapInputs.int128ToUint128(out);
    if (amountOut < params.amountOutMinimum) revert InsufficientOutput(amountOut, params.amountOutMinimum);

    _clearExpectedCallbackPool();
  }
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

**File:** metric-periphery/contracts/MetricOmmSimpleRouter.sol (L130-147)
```text
  function exactOutputSingle(ExactOutputSingleParams calldata params) external payable returns (uint256 amountIn) {
    _checkDeadline(params.deadline);
    uint128 priceLimitX64 = MetricOmmSwapPath.normalizePriceLimit(params.zeroForOne, params.priceLimitX64);

    int128 expectedAmountOut = MetricOmmSwapInputs.asAmountSpecifiedIn(params.amountOut);
    _setNextCallbackContext(params.pool, CALLBACK_MODE_JUST_PAY, msg.sender, params.tokenIn);
    (int128 amount0Delta, int128 amount1Delta) = IMetricOmmPoolActions(params.pool)
      .swap(params.recipient, params.zeroForOne, -expectedAmountOut, priceLimitX64, "", params.extensionData);
    int128 amountOut = MetricOmmSwapResults.extractAmountOut(params.zeroForOne, amount0Delta, amount1Delta);
    if (amountOut != expectedAmountOut) revert InvalidOutputAmount(amountOut, params.amountOut);

    amountIn = MetricOmmSwapInputs.int128ToUint128(
      MetricOmmSwapResults.extractAmountIn(params.zeroForOne, amount0Delta, amount1Delta)
    );

    if (amountIn > params.amountInMaximum) revert InputTooHigh(amountIn, params.amountInMaximum);
    _clearExpectedCallbackPool();
  }
```
