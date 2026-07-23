Audit Report

## Title
SwapAllowlistExtension Gates the Router Address Instead of the Actual User, Enabling Full Allowlist Bypass via MetricOmmSimpleRouter - (File: metric-periphery/contracts/extensions/SwapAllowlistExtension.sol)

## Summary

`SwapAllowlistExtension.beforeSwap` receives `sender` from the pool, which is set to `msg.sender` of the `pool.swap()` call. When a user routes through `MetricOmmSimpleRouter`, `msg.sender` at the pool is the router contract. The extension therefore checks whether the **router** is allowlisted, not the actual swapper. A pool admin who allowlists the router to enable curated access inadvertently grants every unprivileged user unrestricted swap access through the router.

## Finding Description

**`MetricOmmPool.swap`** passes `msg.sender` directly as `sender` to `_beforeSwap`: [1](#0-0) 

**`ExtensionCalling._beforeSwap`** forwards `sender` unchanged to every configured extension: [2](#0-1) 

**`SwapAllowlistExtension.beforeSwap`** evaluates `allowedSwapper[msg.sender][sender]`, where `msg.sender` is the pool and `sender` is whoever called `pool.swap()` — the router when routing through `MetricOmmSimpleRouter`: [3](#0-2) 

**`MetricOmmSimpleRouter.exactInputSingle`** calls `pool.swap()` directly with no mechanism to encode or forward the original caller's identity — `extensionData` is passed through verbatim from the user's call parameters, not populated by the router with `msg.sender`: [4](#0-3) 

The same flaw applies to `exactOutputSingle`, `exactInput`, and `exactOutput` — all router entry points call `pool.swap()` as `msg.sender = router` with no original-user attestation.

By contrast, **`DepositAllowlistExtension.beforeAddLiquidity`** checks the `owner` parameter (second argument), which is explicitly set to the actual liquidity provider's address, not the caller: [5](#0-4) 

This asymmetry makes the swap-side allowlist uniquely broken.

**Two concrete failure modes:**

1. **Allowlist bypass (high impact):** Admin allowlists the router so curated users can access the pool via the official periphery. Because the extension sees only the router address, every user — including non-allowlisted ones — can swap freely through the router.

2. **Broken allowlist (medium impact):** Admin does not allowlist the router. Allowlisted users cannot use the router at all; they must call the pool directly. The official periphery path is silently unusable for curated pools.

## Impact Explanation

A pool configured with `SwapAllowlistExtension` to restrict trading to KYC'd or otherwise curated addresses loses that restriction entirely for any user who routes through `MetricOmmSimpleRouter`. Non-allowlisted traders can move pool prices, extract value against LP positions, and generate swap volume without the intended gate. This is a direct admin-boundary break: the pool admin's access control is bypassed by an unprivileged path through the official periphery, with direct fund-impacting consequences for LPs in curated pools.

## Likelihood Explanation

Medium-to-high. `MetricOmmSimpleRouter` is the official, documented swap periphery. Pool admins who configure a swap allowlist will naturally also want their allowlisted users to be able to use the router, making the router-allowlisting step highly probable. Once the router is allowlisted, the bypass is trivially reachable by any unprivileged user with no special setup, no capital requirement beyond the swap itself, and no special permissions.

## Recommendation

The extension must gate the **original user**, not the direct pool caller. Viable approaches:

1. **Pass the original user through `extensionData`:** The router encodes `msg.sender` into `extensionData`; the extension decodes and verifies it against a registry of trusted routers. Fragile if multiple entry points exist.

2. **Preferred — propagate `originalSender` at the pool level:** Add a first-class `originalSender` field that `MetricOmmPool` propagates through hook arguments, so extensions always see the economic actor regardless of intermediary. This is the most robust fix and eliminates the problem for all future extensions.

3. **Router-aware allowlist:** Extend the extension to recognize approved router contracts and, when `sender` is a known router, require that the router also attests the original user via a signed payload in `extensionData`.

## Proof of Concept

```
Setup:
  pool = MetricOmmPool with SwapAllowlistExtension as extension1
  admin calls setAllowedToSwap(pool, router, true)
    → allowedSwapper[pool][router] = true
  alice (allowlisted directly): allowedSwapper[pool][alice] = true
  bob (NOT allowlisted): allowedSwapper[pool][bob] = false

Attack:
  bob calls MetricOmmSimpleRouter.exactInputSingle({pool: pool, ...})
  → router calls pool.swap(recipient, ...)       // msg.sender = router
  → pool calls _beforeSwap(sender=router, ...)
  → extension checks allowedSwapper[pool][router] → true
  → swap executes for bob with no revert

Result:
  bob, a non-allowlisted user, successfully swaps on a curated pool.
  The allowlist invariant is broken.
```

Reproducible as a Foundry unit test: deploy `MetricOmmPool` with `SwapAllowlistExtension`, allowlist the router, call `exactInputSingle` from a non-allowlisted EOA, and assert the swap succeeds (no `NotAllowedToSwap` revert).

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

**File:** metric-core/contracts/ExtensionCalling.sol (L149-176)
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
