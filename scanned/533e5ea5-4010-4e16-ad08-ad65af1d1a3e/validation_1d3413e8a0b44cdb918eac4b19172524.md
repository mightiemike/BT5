### Title
SwapAllowlistExtension Gates the Router Address Instead of the Actual User, Enabling Full Allowlist Bypass via MetricOmmSimpleRouter - (File: metric-periphery/contracts/extensions/SwapAllowlistExtension.sol)

### Summary

`SwapAllowlistExtension.beforeSwap` checks the `sender` argument, which the pool sets to `msg.sender` of the `swap()` call. When a user routes through `MetricOmmSimpleRouter`, `msg.sender` at the pool is the router contract, not the original user. The extension therefore checks whether the **router** is allowlisted, not the actual swapper. If the pool admin allowlists the router (the natural step to let their curated users access it), every unprivileged user can bypass the allowlist by routing through the router.

### Finding Description

**Call chain:**

```
User (non-allowlisted)
  → MetricOmmSimpleRouter.exactInputSingle(params)
      → IMetricOmmPoolActions(params.pool).swap(recipient, ...)   // msg.sender = router
          → MetricOmmPool._beforeSwap(msg.sender=router, ...)
              → SwapAllowlistExtension.beforeSwap(sender=router, ...)
                  → allowedSwapper[pool][router]  ← checks router, not user
```

In `MetricOmmPool.swap`, the pool passes `msg.sender` as the `sender` argument to `_beforeSwap`: [1](#0-0) 

`_beforeSwap` forwards that value unchanged to every configured extension: [2](#0-1) 

`SwapAllowlistExtension.beforeSwap` then evaluates `allowedSwapper[msg.sender][sender]`, where `msg.sender` is the pool and `sender` is the router: [3](#0-2) 

The router calls the pool directly with no mechanism to forward the original user's identity: [4](#0-3) 

**Two concrete failure modes arise:**

1. **Allowlist bypass (high impact):** The pool admin allowlists the router address so that their curated users can access the pool through the official periphery. Because the extension sees only the router, every user — including non-allowlisted ones — can now swap freely through the router.

2. **Broken allowlist (medium impact):** The pool admin does not allowlist the router. Allowlisted users cannot use the router at all; they must call the pool directly. The official periphery path is silently unusable for curated pools.

Note that `DepositAllowlistExtension` does **not** share this flaw: it checks the `owner` parameter (second argument), which callers supply explicitly and which the liquidity adder correctly sets to the actual user's address. The asymmetry makes the swap-side allowlist uniquely broken. [5](#0-4) 

### Impact Explanation

A pool configured with `SwapAllowlistExtension` to restrict trading to KYC'd or otherwise curated addresses loses that restriction entirely for any user who routes through `MetricOmmSimpleRouter`. The attacker pays no special cost: they simply call the public router with a pool address that has the allowlist extension. All swap volume — and any associated fee revenue or price impact — flows through without the intended gate. This is a direct policy bypass on curated pools with fund-impacting consequences (unauthorized traders can move pool prices, extract value, or drain liquidity against LP positions).

### Likelihood Explanation

Medium-to-high. `MetricOmmSimpleRouter` is the official, documented swap periphery. Pool admins who configure a swap allowlist will naturally also want their allowlisted users to be able to use the router, making the router-allowlisting step highly probable. Once the router is allowlisted, the bypass is trivially reachable by any unprivileged user with no special setup.

### Recommendation

The extension must gate the **original user**, not the direct pool caller. Two viable approaches:

1. **Pass the original user through `extensionData`:** The router encodes `msg.sender` into `extensionData`; the extension decodes and verifies it. This requires a trusted encoding convention and is fragile if the router is not the only entry point.

2. **Check `sender` against a router-aware allowlist:** Extend the extension to recognize approved router contracts and, when `sender` is a known router, require that the router also attests the original user (e.g., via a signed payload in `extensionData`).

3. **Preferred — gate at the pool level, not the extension:** Add a first-class `originalSender` field that the pool propagates through the hook arguments, so extensions always see the economic actor regardless of intermediary.

### Proof of Concept

```
Setup:
  pool = MetricOmmPool with SwapAllowlistExtension as extension1
  admin allowlists router: allowedSwapper[pool][router] = true
  alice (allowlisted directly): allowedSwapper[pool][alice] = true
  bob (NOT allowlisted): allowedSwapper[pool][bob] = false

Attack:
  bob calls MetricOmmSimpleRouter.exactInputSingle({pool: pool, ...})
  → router calls pool.swap(...)          // msg.sender = router
  → pool calls _beforeSwap(router, ...)
  → extension checks allowedSwapper[pool][router] → true
  → swap executes for bob with no revert

Result:
  bob, a non-allowlisted user, successfully swaps on a curated pool.
  The allowlist invariant is broken.
``` [6](#0-5) [7](#0-6)

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

**File:** metric-periphery/contracts/MetricOmmSimpleRouter.sol (L71-86)
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
    int128 out = MetricOmmSwapResults.extractAmountOut(params.zeroForOne, amount0Delta, amount1Delta);
    amountOut = MetricOmmSwapInputs.int128ToUint128(out);
    if (amountOut < params.amountOutMinimum) revert InsufficientOutput(amountOut, params.amountOutMinimum);

    _clearExpectedCallbackPool();
  }
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
