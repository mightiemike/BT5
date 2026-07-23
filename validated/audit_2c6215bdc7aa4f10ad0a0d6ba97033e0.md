Audit Report

## Title
`SwapAllowlistExtension.beforeSwap` checks router address as `sender` instead of originating user, enabling full allowlist bypass via `MetricOmmSimpleRouter` — (`metric-periphery/contracts/extensions/SwapAllowlistExtension.sol`)

## Summary
`SwapAllowlistExtension.beforeSwap` gates swaps by checking the `sender` argument, which the pool sets to `msg.sender` of `pool.swap()`. When users route through `MetricOmmSimpleRouter`, the router becomes `msg.sender` of `pool.swap()`, so the extension checks the router's allowlist status rather than the originating user's. Any pool admin who allowlists the router to enable legitimate router-based swaps simultaneously grants unrestricted swap access to every non-allowlisted address that calls the same public router.

## Finding Description

**Step 1 — Pool passes `msg.sender` as `sender` to the extension.**

`MetricOmmPool.swap` calls `_beforeSwap(msg.sender, ...)`: [1](#0-0) 

`ExtensionCalling._beforeSwap` forwards `sender` unchanged to every configured extension: [2](#0-1) 

**Step 2 — `SwapAllowlistExtension.beforeSwap` checks that `sender` value.**

```solidity
if (!allowAllSwappers[msg.sender] && !allowedSwapper[msg.sender][sender]) {
    revert IMetricOmmPoolActions.NotAllowedToSwap();
}
``` [3](#0-2) 

Here `msg.sender` is the pool (correct mapping key) and `sender` is whoever called `pool.swap()` — the router, not the original user.

**Step 3 — `MetricOmmSimpleRouter` calls `pool.swap()` directly, making itself the `sender`.**

`exactInputSingle`, `exactInput`, and `exactOutputSingle` all call `IMetricOmmPoolActions(params.pool).swap(...)` with the router as `msg.sender`: [4](#0-3) [5](#0-4) [6](#0-5) 

**Step 4 — The irresolvable dilemma.**

| Admin choice | Allowlisted users via router | Non-allowlisted users via router |
|---|---|---|
| Do **not** allowlist router | Blocked — cannot use router | Correctly blocked |
| **Allowlist router** | Can swap | **Also pass — bypass achieved** |

There is no configuration that simultaneously permits allowlisted users to use the router and blocks non-allowlisted users. The wrong value is `allowedSwapper[pool][router]` being checked instead of `allowedSwapper[pool][originalUser]`. [7](#0-6) 

## Impact Explanation
Any non-allowlisted address can bypass the swap allowlist on any pool that has `SwapAllowlistExtension` configured and the router allowlisted. The allowlist is the pool's primary access-control boundary for swaps. Bypassing it lets unprivileged addresses execute swaps the pool operator explicitly intended to block. Pools deployed for regulated or permissioned trading (KYC, institutional-only) lose their access control entirely. This is a direct admin-boundary break by an unprivileged path, matching the allowed impact gate. [8](#0-7) 

## Likelihood Explanation
`MetricOmmSimpleRouter` is a public, permissionless contract — any address can call it. The only prerequisite is that the pool admin allowlists the router, which is the natural operational step any admin would take to let their allowlisted users access the router. No special privileges, flash loans, or unusual conditions are required. The bypass is reachable by any user as soon as the pool is configured for normal router use. [9](#0-8) 

## Recommendation
The extension must check the **original user's identity**, not the immediate caller of `pool.swap()`. Two viable approaches:

1. **Pass the original user in `extensionData`**: The router encodes `msg.sender` into `extensionData`; the extension decodes and checks it. This requires a convention between router and extension.
2. **Add an `originator` field to the hook interface**: The pool passes both `sender` (immediate caller) and `originator` (the address the router recorded as the economic actor). The extension checks `originator`.

Until fixed, pools that need a swap allowlist must not allowlist the router, which means allowlisted users cannot use the router — a broken core swap flow. [10](#0-9) 

## Proof of Concept
```
1. Pool admin deploys pool with SwapAllowlistExtension as beforeSwap hook.
2. Pool admin calls setAllowedToSwap(pool, alice, true)   // alice is KYC'd
3. Pool admin calls setAllowedToSwap(pool, router, true)  // so alice can use the router
4. Bob (not allowlisted) calls router.exactInputSingle({pool: pool, ...})
5. Router calls pool.swap(recipient, ...) — msg.sender = router
6. _beforeSwap(sender=router, ...) → SwapAllowlistExtension.beforeSwap
7. Check: allowedSwapper[pool][router] == true  → passes
8. Bob's swap executes successfully, bypassing the allowlist.
``` [1](#0-0) [3](#0-2) [4](#0-3)

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

**File:** metric-periphery/contracts/extensions/SwapAllowlistExtension.sol (L12-13)
```text
  mapping(address pool => mapping(address swapper => bool)) public allowedSwapper;
  mapping(address pool => bool) public allowAllSwappers;
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

**File:** metric-periphery/contracts/MetricOmmSimpleRouter.sol (L136-137)
```text
    (int128 amount0Delta, int128 amount1Delta) = IMetricOmmPoolActions(params.pool)
      .swap(params.recipient, params.zeroForOne, -expectedAmountOut, priceLimitX64, "", params.extensionData);
```
