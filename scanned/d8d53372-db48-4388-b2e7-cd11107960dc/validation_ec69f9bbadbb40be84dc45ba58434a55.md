### Title
`SwapAllowlistExtension` gates the router contract instead of the actual user, allowing any unprivileged address to bypass the swap allowlist via `MetricOmmSimpleRouter` — (`metric-periphery/contracts/extensions/SwapAllowlistExtension.sol`)

---

### Summary

`SwapAllowlistExtension.beforeSwap()` checks the `sender` argument, which the pool sets to `msg.sender` of `pool.swap()`. When a user routes through `MetricOmmSimpleRouter`, the router is `msg.sender` of `pool.swap()`, so the extension checks the router's allowlist status rather than the actual user's. If the router is allowlisted (a prerequisite for any router-mediated swap to succeed on a curated pool), every unprivileged address can bypass the allowlist by routing through the router.

---

### Finding Description

`MetricOmmPool.swap()` passes `msg.sender` as the `sender` argument to `_beforeSwap()`:

```solidity
// metric-core/contracts/MetricOmmPool.sol
_beforeSwap(
    msg.sender,   // <-- whoever called pool.swap()
    recipient,
    ...
);
```

`ExtensionCalling._beforeSwap()` forwards that value verbatim to every configured extension:

```solidity
// metric-core/contracts/ExtensionCalling.sol
abi.encodeCall(IMetricOmmExtensions.beforeSwap, (sender, recipient, ...))
```

`SwapAllowlistExtension.beforeSwap()` then checks that `sender` against the per-pool allowlist:

```solidity
// metric-periphery/contracts/extensions/SwapAllowlistExtension.sol
function beforeSwap(address sender, ...) external view override returns (bytes4) {
    if (!allowAllSwappers[msg.sender] && !allowedSwapper[msg.sender][sender]) {
        revert IMetricOmmPoolActions.NotAllowedToSwap();
    }
    ...
}
```

When a user calls `MetricOmmSimpleRouter.exactInputSingle()`, the router calls `pool.swap()` directly:

```solidity
// metric-periphery/contracts/MetricOmmSimpleRouter.sol
IMetricOmmPoolActions(params.pool).swap(
    params.recipient,
    params.zeroForOne,
    ...
    params.extensionData
);
```

At this point `msg.sender` inside `pool.swap()` is the router, so `sender = router`. The extension evaluates `allowedSwapper[pool][router]`, not `allowedSwapper[pool][actual_user]`.

A pool admin who wants allowlisted users to be able to swap through the router must add the router to the allowlist. Once the router is allowlisted, the check is trivially satisfied for every caller — allowlisted or not — because the router is always the immediate caller of `pool.swap()`. The allowlist is completely bypassed.

The same structural problem applies to `exactInput`, `exactOutputSingle`, and `exactOutput` — all four router entry points call `pool.swap()` with `msg.sender = router`.

---

### Impact Explanation

**High.** A pool admin deploys a curated pool (e.g., KYC-only, market-maker-only) and configures `SwapAllowlistExtension` to restrict swaps to a specific set of addresses. Any address outside that set can bypass the restriction by calling any of the four `MetricOmmSimpleRouter` swap functions. The router is a public, permissionless contract. The bypass requires no special privilege, no token approval beyond the normal swap approval, and no front-running. Every swap that executes on a curated pool by a non-allowlisted user is a direct policy violation with real economic consequences: the pool's liquidity is consumed by actors the pool admin explicitly excluded.

---

### Likelihood Explanation

**Medium.** The bypass is only reachable when the pool admin has allowlisted the router address. However, allowlisting the router is the only way to let allowlisted users trade through the router (a standard, documented periphery path). Any pool admin who wants to support router-mediated swaps for their allowlisted users is forced to allowlist the router, which simultaneously opens the bypass to everyone. The condition is therefore a natural consequence of normal pool configuration, not an exotic mistake.

---

### Recommendation

The extension must gate the original economic actor, not the immediate caller of `pool.swap()`. Two complementary fixes:

1. **Router-side**: `MetricOmmSimpleRouter` should store the originating `msg.sender` in transient storage (it already uses transient storage for callback context) and expose it via a public getter. The extension can then call back to the pool's caller to retrieve the real user.

2. **Extension-side (simpler)**: `SwapAllowlistExtension.beforeSwap()` should check `recipient` or require the pool to pass the original initiator. Alternatively, the extension interface could be extended with an `initiator` field distinct from `sender`.

3. **Documentation guard (minimum)**: The `SwapAllowlistExtension` NatSpec must explicitly state that allowlisting the router grants swap access to all router users, so pool admins do not allowlist the router under the mistaken belief that the per-user allowlist still applies.

---

### Proof of Concept

```
Setup:
  - Deploy pool with SwapAllowlistExtension as beforeSwap hook
  - Pool admin calls setAllowedToSwap(pool, alice, true)      // alice is KYC'd
  - Pool admin calls setAllowedToSwap(pool, router, true)     // required for alice to use the router
  - bob is NOT allowlisted

Attack:
  1. bob calls MetricOmmSimpleRouter.exactInputSingle({pool: pool, ...})
  2. Router calls pool.swap(recipient, ...) — msg.sender = router
  3. Pool calls _beforeSwap(sender=router, ...)
  4. Extension evaluates: allowedSwapper[pool][router] == true  → passes
  5. bob's swap executes successfully on the curated pool

Expected: revert NotAllowedToSwap()
Actual:   swap succeeds — allowlist fully bypassed
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
