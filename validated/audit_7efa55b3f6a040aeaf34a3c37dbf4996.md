Audit Report

## Title
`SwapAllowlistExtension` Checks Router Address Instead of End User, Allowing Any User to Bypass the Swap Allowlist via `MetricOmmSimpleRouter` — (File: `metric-periphery/contracts/extensions/SwapAllowlistExtension.sol`)

## Summary
`SwapAllowlistExtension.beforeSwap` gates swaps by checking the `sender` argument, which the pool always sets to its own `msg.sender`. When `MetricOmmSimpleRouter` is used, the pool's `msg.sender` is the router, not the end user. If the pool admin allowlists the router (a natural step to support standard UX), every on-chain user can bypass the per-user allowlist by routing through the router, rendering the extension's access control ineffective.

## Finding Description
`SwapAllowlistExtension.beforeSwap` checks `allowedSwapper[msg.sender][sender]`, where `msg.sender` is the pool and `sender` is the first argument forwarded by the pool: [1](#0-0) 

`MetricOmmPool.swap` always passes its own `msg.sender` as the `sender` argument to `_beforeSwap`: [2](#0-1) 

`ExtensionCalling._beforeSwap` forwards that value unchanged to every configured extension: [3](#0-2) 

`MetricOmmSimpleRouter.exactInputSingle` calls `pool.swap(...)` directly, making the router the `msg.sender` seen by the pool: [4](#0-3) 

The same pattern holds for `exactInput`, `exactOutputSingle`, and `exactOutput` — in every case the pool receives `msg.sender = router`, so `sender` forwarded to the extension is the router address, not the end user. [5](#0-4) 

The allowlist check therefore gates the router, not the user. Once `allowedSwapper[pool][router] = true`, the condition evaluates to `true` for every caller who routes through the router, regardless of their own allowlist status. No existing guard in the extension, pool, or router prevents this substitution.

## Impact Explanation
Any user not on the allowlist can trade in a curated pool by calling any router entry point once the router is allowlisted. The pool admin cannot simultaneously restrict trading to specific users and support the standard router UX — the two goals are mutually exclusive under the current design. Pools that rely on the allowlist to prevent adverse-selection flow (e.g., restricting to known market makers) will silently accept all public order flow, exposing LPs to losses they configured the extension to prevent. This constitutes broken core pool functionality and direct loss of LP assets above Sherlock thresholds. [6](#0-5) 

## Likelihood Explanation
The trigger requires the pool admin to add the router to the allowlist. This is a natural, expected configuration step: any operator who deploys a curated pool and also wants their allowlisted users to access the standard router UI will perform exactly this step. No privileged attacker capability is required beyond being a normal user of the router. The scenario is repeatable by any on-chain address. [7](#0-6) 

## Recommendation
Replace the `sender` check with a check on the end user identity. Two approaches:

1. **Pass the originating user through `extensionData`**: require callers (including the router) to include the real user address in `extensionData`, and have the extension decode and verify it. The router already forwards `extensionData` unchanged, so this is compatible with the existing call path.

2. **Check `sender` and fall back to a decoded user from `extensionData`**: if `sender` is a known router, extract the real user from `extensionData` and check that address instead.

Either way, the allowlist must gate the economically relevant actor (the end user who controls the trade and receives the output), not the intermediate contract that relays the call. [8](#0-7) 

## Proof of Concept
```
Setup
─────
1. Pool admin deploys pool with SwapAllowlistExtension.
2. Pool admin allowlists Alice (a legitimate trader):
       setAllowedToSwap(pool, alice, true)
3. Pool admin allowlists the router so Alice can use the standard UI:
       setAllowedToSwap(pool, router, true)

Attack
──────
4. Bob (not allowlisted) calls:
       router.exactInputSingle({pool: pool, zeroForOne: true, ...})

5. Router calls pool.swap(...) — msg.sender to pool = router.

6. Pool calls _beforeSwap(router, bob_recipient, ...).

7. SwapAllowlistExtension.beforeSwap receives sender = router.
   Check: allowedSwapper[pool][router] == true  → passes.

8. Bob's swap executes in the curated pool.
   Bob was never on the allowlist; the guard is fully bypassed.
``` [9](#0-8) [1](#0-0)

### Citations

**File:** metric-periphery/contracts/extensions/SwapAllowlistExtension.sol (L9-13)
```text
/// @title SwapAllowlistExtension
/// @notice Gates `swap` by swapper address, per pool.
contract SwapAllowlistExtension is BaseMetricExtension, ISwapAllowlistExtension {
  mapping(address pool => mapping(address swapper => bool)) public allowedSwapper;
  mapping(address pool => bool) public allowAllSwappers;
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

**File:** metric-core/contracts/ExtensionCalling.sol (L149-165)
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

**File:** metric-periphery/contracts/MetricOmmSimpleRouter.sol (L104-112)
```text
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
