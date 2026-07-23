### Title
`SwapAllowlistExtension` Gates Router Address Instead of User Address, Enabling Full Allowlist Bypass via `MetricOmmSimpleRouter` — (File: `metric-periphery/contracts/extensions/SwapAllowlistExtension.sol`)

### Summary

`SwapAllowlistExtension.beforeSwap()` checks the `sender` argument, which is `msg.sender` of `pool.swap()`. When users swap through `MetricOmmSimpleRouter`, `sender` is the **router's address**, not the user's. A pool admin who allowlists the router to enable router-mediated swaps for their curated pool inadvertently opens the pool to **all** users, completely nullifying the allowlist.

### Finding Description

The `SwapAllowlistExtension` is the production guard for curated pools that must restrict which addresses may trade. Its `beforeSwap` hook is:

```solidity
// SwapAllowlistExtension.sol line 37
if (!allowAllSwappers[msg.sender] && !allowedSwapper[msg.sender][sender]) {
    revert IMetricOmmPoolActions.NotAllowedToSwap();
}
```

`msg.sender` here is the pool (correct pool-namespace key). `sender` is the first argument forwarded by `ExtensionCalling._beforeSwap()`:

```solidity
// ExtensionCalling.sol line 160-176
_callExtensionsInOrder(
    BEFORE_SWAP_ORDER,
    abi.encodeCall(
        IMetricOmmExtensions.beforeSwap,
        (sender, recipient, ...)   // sender = msg.sender of pool.swap()
    )
);
```

And in `MetricOmmPool.swap()`:

```solidity
// MetricOmmPool.sol line 230-240
_beforeSwap(
    msg.sender,   // <-- whoever called pool.swap()
    recipient, ...
);
```

When a user calls `MetricOmmSimpleRouter.exactInputSingle()` (or any other router entry point), the router calls `pool.swap()` directly:

```solidity
// MetricOmmSimpleRouter.sol line 72-80
(int128 amount0Delta, int128 amount1Delta) = IMetricOmmPoolActions(params.pool)
    .swap(params.recipient, params.zeroForOne, ..., params.extensionData);
```

`msg.sender` of that `pool.swap()` call is the **router**, so `sender` delivered to `beforeSwap` is the **router address**. The allowlist therefore evaluates `allowedSwapper[pool][router]`, not `allowedSwapper[pool][user]`.

**The catch-22 for the pool admin:**

| Admin action | Allowlisted users via router | Non-allowlisted users via router |
|---|---|---|
| Router NOT allowlisted | ❌ Blocked | ❌ Blocked |
| Router allowlisted | ✅ Allowed | ✅ Allowed — **bypass** |

If the admin allowlists the router (the only way to let allowlisted users trade through the supported periphery), every user on the network can bypass the allowlist by routing through `MetricOmmSimpleRouter`. The wrong identity — router instead of user — is checked, exactly analogous to the seed report's wrong type hash being used in the domain separator.

### Impact Explanation

A curated pool's entire swap allowlist is rendered ineffective. Any unprivileged address can trade on a pool that was configured to restrict access to a specific set of counterparties (e.g., KYC'd users, institutional partners). This allows unauthorized users to drain LP value at oracle-anchored prices, constituting a direct loss of LP principal on pools whose security model depends on the allowlist.

### Likelihood Explanation

The `MetricOmmSimpleRouter` is the primary user-facing swap interface. A pool admin who deploys a curated pool and wants their allowlisted users to be able to use the standard router will naturally call `setAllowedToSwap(pool, router, true)`. The admin's intent ("let my allowlisted users use the router") and the actual effect ("let everyone use the router") are completely different, and nothing in the extension's interface or documentation warns against this. The trigger is a single, reasonable admin action on a live production pool.

### Recommendation

The `beforeSwap` hook must gate the **economic actor**, not the immediate caller of `pool.swap()`. Two options:

1. **Pass user identity through `extensionData`**: The router encodes `msg.sender` into `extensionData`; the extension decodes and checks it. This requires a coordinated change to the router and extension.
2. **Document the incompatibility explicitly**: If the design intent is to gate the immediate caller only, the extension NatSpec must state that allowlisting the router opens the pool to all users, and pool admins must allowlist individual users who call the pool directly — not the router.

### Proof of Concept

```solidity
// 1. Admin deploys pool with SwapAllowlistExtension
SwapAllowlistExtension ext = new SwapAllowlistExtension(factory);
// Pool created with ext as beforeSwap hook

// 2. Admin allowlists the router so allowlisted users can trade via the standard UI
ext.setAllowedToSwap(address(pool), address(router), true);

// 3. Attacker (not in allowlist) calls the router directly
// router.exactInputSingle() → pool.swap(msg.sender=router) → beforeSwap(sender=router)
// allowedSwapper[pool][router] == true → passes → swap executes
router.exactInputSingle(ExactInputSingleParams({
    pool: address(pool),
    recipient: attacker,
    zeroForOne: true,
    amountIn: 1_000e18,
    ...
}));
// Allowlist completely bypassed; attacker trades on a curated pool
``` [1](#0-0) [2](#0-1) [3](#0-2) [4](#0-3)

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

**File:** metric-core/contracts/MetricOmmPool.sol (L228-241)
```text
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
