Audit Report

## Title
`SwapAllowlistExtension.beforeSwap` Checks Router Address Instead of End-User, Enabling Full Allowlist Bypass via `MetricOmmSimpleRouter` — (`metric-periphery/contracts/extensions/SwapAllowlistExtension.sol`)

## Summary
`SwapAllowlistExtension.beforeSwap` gates swaps by checking the `sender` argument, which is the direct `msg.sender` of `MetricOmmPool.swap()`. When a user routes through `MetricOmmSimpleRouter`, the router is the direct caller of `pool.swap()`, so `sender` equals the router address. Any pool admin who allowlists the router to enable router-based swaps inadvertently grants unrestricted swap access to every user of the router, completely defeating the per-user allowlist.

## Finding Description

**Call chain:**

1. `MetricOmmPool.swap()` dispatches the before-swap hook as:
   ```solidity
   _beforeSwap(msg.sender, recipient, ...);  // sender = direct caller of pool.swap()
   ``` [1](#0-0) 

2. `ExtensionCalling._beforeSwap` forwards `sender` verbatim to every configured extension:
   ```solidity
   abi.encodeCall(IMetricOmmExtensions.beforeSwap, (sender, recipient, ...))
   ``` [2](#0-1) 

3. `SwapAllowlistExtension.beforeSwap` checks:
   ```solidity
   if (!allowAllSwappers[msg.sender] && !allowedSwapper[msg.sender][sender]) {
       revert IMetricOmmPoolActions.NotAllowedToSwap();
   }
   ```
   Here `msg.sender` is the pool; `sender` is whoever called `pool.swap()`. [3](#0-2) 

4. `MetricOmmSimpleRouter.exactInputSingle` calls `pool.swap()` directly, making the router the `msg.sender` of the pool:
   ```solidity
   IMetricOmmPoolActions(params.pool).swap(params.recipient, ...);
   ``` [4](#0-3) 

**Result:** The extension evaluates `allowedSwapper[pool][router]`, not `allowedSwapper[pool][end-user]`. There is no field in the `beforeSwap` signature that carries the original end-user's address, and no mechanism in the router to inject it.

**Bypass path:**
- Pool admin deploys a pool with `SwapAllowlistExtension` to restrict swaps to a curated set of addresses.
- Admin allowlists the router (`allowedSwapper[pool][router] = true`) to permit router-based swaps for allowlisted users.
- Any unprivileged address — including addresses the admin explicitly never allowlisted — calls `MetricOmmSimpleRouter.exactInputSingle()` targeting that pool.
- The extension checks `allowedSwapper[pool][router]` → `true` → swap proceeds.
- The per-user allowlist is entirely bypassed.

The same bypass applies to `exactInput`, `exactOutputSingle`, and `exactOutput` since all of them call `pool.swap()` with the router as `msg.sender`. [5](#0-4) 

## Impact Explanation
The `SwapAllowlistExtension` is the protocol's mechanism for pools that require restricted swap access (e.g., permissioned or KYC-gated pools). Once the router is allowlisted — a necessary step for any admin who wants to support router-based swaps — the allowlist provides zero protection against unprivileged traders. Any address can swap through the pool by routing through `MetricOmmSimpleRouter`, bypassing the intended access control entirely. This constitutes broken core pool functionality: the allowlist guard is rendered inoperative for the primary user-facing swap path.

## Likelihood Explanation
The condition is straightforward to trigger: the pool admin must have allowlisted the router (a natural and expected configuration for any pool that intends to support the standard periphery router), and any unprivileged user simply calls the router. No special privileges, flash loans, or complex setup are required. The bypass is repeatable on every swap and affects all pools using `SwapAllowlistExtension` with the router allowlisted.

## Recommendation
The extension must verify the actual end-user, not the intermediary. Two approaches:

1. **Pass the original payer via `extensionData`:** The router encodes `msg.sender` (the end-user) into `extensionData` before calling `pool.swap()`, and `SwapAllowlistExtension.beforeSwap` decodes and checks it. This requires a convention between router and extension.

2. **Check `recipient` instead of `sender`:** If the pool's intended model is that the recipient is the gated party, check `recipient` (the second argument to `beforeSwap`) rather than `sender`. However, this changes the semantics of the allowlist.

3. **Introduce a dedicated `originalSender` field in the hook signature** at the core level, populated by the pool from transient storage set by the router before calling `swap()`.

## Proof of Concept

```solidity
// 1. Deploy pool with SwapAllowlistExtension configured.
// 2. Pool admin allowlists the router:
swapAllowlistExtension.setAllowedToSwap(pool, address(router), true);

// 3. Unprivileged address (never individually allowlisted) calls the router:
// vm.prank(unprivilegedUser);
router.exactInputSingle(ExactInputSingleParams({
    pool: pool,
    recipient: unprivilegedUser,
    zeroForOne: true,
    amountIn: 1e18,
    amountOutMinimum: 0,
    priceLimitX64: 0,
    deadline: block.timestamp,
    extensionData: ""
}));
// Swap succeeds — allowlist bypassed.
// allowedSwapper[pool][unprivilegedUser] is false, but check passed via allowedSwapper[pool][router].
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

**File:** metric-core/contracts/ExtensionCalling.sol (L160-176)
```text
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

**File:** metric-periphery/contracts/extensions/SwapAllowlistExtension.sol (L37-39)
```text
    if (!allowAllSwappers[msg.sender] && !allowedSwapper[msg.sender][sender]) {
      revert IMetricOmmPoolActions.NotAllowedToSwap();
    }
```

**File:** metric-periphery/contracts/MetricOmmSimpleRouter.sol (L72-80)
```text
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

**File:** metric-periphery/contracts/MetricOmmSimpleRouter.sol (L99-112)
```text
    for (uint256 i = 0; i <= last; i++) {
      address pool = params.pools[i];
      bool zeroForOne = MetricOmmSwapPath.resolveZeroForOneBitmap(params.zeroForOneBitMap, i);

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
