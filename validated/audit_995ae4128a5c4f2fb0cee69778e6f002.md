### Title
SwapAllowlistExtension Wrong-Actor Binding Allows Full Allowlist Bypass via MetricOmmSimpleRouter — (`metric-periphery/contracts/extensions/SwapAllowlistExtension.sol`)

---

### Summary

`SwapAllowlistExtension.beforeSwap` gates swaps by checking the `sender` argument, which is `msg.sender` of the pool's `swap()` call. When a user routes through `MetricOmmSimpleRouter`, the pool's `msg.sender` is the **router contract**, not the actual user. If the pool admin allowlists the router (the natural action to enable router-mediated swaps for permitted users), every unpermitted user can bypass the per-user allowlist by routing through the router.

---

### Finding Description

`SwapAllowlistExtension.beforeSwap` receives `sender` as the first argument and checks it against the per-pool allowlist:

```solidity
// metric-periphery/contracts/extensions/SwapAllowlistExtension.sol:31-41
function beforeSwap(address sender, address, bool, int128, uint128, uint256, uint128, uint128, bytes calldata)
    external view override returns (bytes4)
{
    if (!allowAllSwappers[msg.sender] && !allowedSwapper[msg.sender][sender]) {
        revert IMetricOmmPoolActions.NotAllowedToSwap();
    }
    return IMetricOmmExtensions.beforeSwap.selector;
}
```

Inside this call, `msg.sender` is the pool (the extension is called by the pool via `CallExtension.callExtension`). The `sender` argument is whatever the pool passed as the first parameter to `beforeSwap`.

The pool always passes its own `msg.sender` as `sender`:

```solidity
// metric-core/contracts/MetricOmmPool.sol:230-240
_beforeSwap(
    msg.sender,   // ← this becomes `sender` in the extension
    recipient,
    ...
);
```

And `ExtensionCalling._beforeSwap` forwards it verbatim:

```solidity
// metric-core/contracts/ExtensionCalling.sol:160-176
abi.encodeCall(
    IMetricOmmExtensions.beforeSwap,
    (sender, recipient, ...)
)
```

When a user calls `MetricOmmSimpleRouter.exactInputSingle()`, the router calls `pool.swap()` directly:

```solidity
// metric-periphery/contracts/MetricOmmSimpleRouter.sol:72-80
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

The pool's `msg.sender` is the **router address**. The extension therefore evaluates:

```
allowedSwapper[pool][router]
```

not `allowedSwapper[pool][actualUser]`.

**The bypass path:**

1. Pool admin configures `SwapAllowlistExtension` to restrict swaps to specific users (e.g., KYC'd counterparties).
2. Pool admin also allowlists the router address so that permitted users can swap through the router — a natural and expected operational step.
3. Any unpermitted user calls `router.exactInputSingle()` targeting the pool.
4. The pool passes `sender = router` to the extension.
5. The extension evaluates `allowedSwapper[pool][router]` = `true` → passes.
6. The unpermitted user's swap executes against the curated pool.

The same bypass applies to `exactInput`, `exactOutputSingle`, and `exactOutput` on the router, and to any multi-hop path where the router is the immediate caller of each pool's `swap()`.

---

### Impact Explanation

A pool deployer who configures `SwapAllowlistExtension` intends to restrict swap access to a curated set of addresses. Once the router is allowlisted (required for permitted users to use the standard periphery), the allowlist guard fails open for **all** router-mediated swaps. Any unpermitted address can trade in the pool by routing through `MetricOmmSimpleRouter`. This breaks the core curation invariant of the pool and constitutes an admin-boundary break: an unprivileged path (the public router) bypasses the access control the pool admin configured.

---

### Likelihood Explanation

The trigger requires the pool admin to allowlist the router. This is the expected operational action: without it, even permitted users cannot use the router (the extension would check `allowedSwapper[pool][router]` = `false` and revert). The pool admin is therefore forced into a binary choice — either block all router-mediated swaps (including for permitted users) or open the pool to all users via the router. There is no correct configuration that enforces per-user restrictions through the router. Any pool that uses `SwapAllowlistExtension` and wants router support is affected.

---

### Recommendation

The extension must gate the **original user**, not the immediate caller of `pool.swap()`. Two viable approaches:

1. **Pass the original user through `extensionData`**: The router encodes `msg.sender` into `extensionData`; the extension decodes and verifies it. This requires the router to be trusted to supply the correct address.
2. **Check both `sender` and a verified origin**: The extension checks `allowedSwapper[pool][sender]` (direct path) and also accepts a signed or router-attested user address from `extensionData` (router path), with the router's address itself not being allowlistable.

The simplest safe fix is to **not allowlist the router** and document that `SwapAllowlistExtension` only enforces restrictions on direct `pool.swap()` callers, not on router-mediated swaps.

---

### Proof of Concept

```
Setup:
  pool = MetricOmmPool with SwapAllowlistExtension as EXTENSION_1
  permittedUser  = allowedSwapper[pool][permittedUser]  = true
  router         = allowedSwapper[pool][router]          = true   ← required for permittedUser to use router
  attacker       = allowedSwapper[pool][attacker]        = false

Attack:
  attacker calls router.exactInputSingle({pool: pool, ...})
    → router calls pool.swap(recipient, ...)          // msg.sender = router
    → pool calls extension.beforeSwap(sender=router, ...)
    → extension checks allowedSwapper[pool][router]   // = true → passes
    → swap executes for attacker

Expected: revert NotAllowedToSwap()
Actual:   swap succeeds
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

**File:** metric-core/contracts/ExtensionCalling.sol (L159-177)
```text
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
