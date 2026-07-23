### Title
`SwapAllowlistExtension` Gates the Router's Address Instead of the Actual Swapper, Enabling Full Allowlist Bypass — (File: metric-periphery/contracts/extensions/SwapAllowlistExtension.sol)

---

### Summary

`SwapAllowlistExtension.beforeSwap` checks the `sender` parameter against the per-pool allowlist. Because `MetricOmmPool.swap` sets `sender = msg.sender` of the pool call, any swap routed through `MetricOmmSimpleRouter` presents the **router address** as `sender`, not the actual user. If the pool admin allowlists the router (the natural action to enable router-mediated swaps for allowlisted users), every unprivileged user can bypass the allowlist by routing through the router.

---

### Finding Description

`MetricOmmPool.swap` passes its own `msg.sender` as the `sender` argument to `_beforeSwap`:

```solidity
// MetricOmmPool.sol line 230-240
_beforeSwap(
    msg.sender,   // ← whoever called pool.swap()
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

`ExtensionCalling._beforeSwap` forwards that value unchanged to every configured extension:

```solidity
// ExtensionCalling.sol line 160-176
abi.encodeCall(
    IMetricOmmExtensions.beforeSwap,
    (sender, recipient, zeroForOne, ...)
)
```

`SwapAllowlistExtension.beforeSwap` then checks that `sender` against the allowlist keyed by the pool (`msg.sender` of the extension call):

```solidity
// SwapAllowlistExtension.sol line 37
if (!allowAllSwappers[msg.sender] && !allowedSwapper[msg.sender][sender]) {
    revert IMetricOmmPoolActions.NotAllowedToSwap();
}
```

When a user calls `MetricOmmSimpleRouter.exactInputSingle`, the router calls `pool.swap(...)` directly:

```solidity
// MetricOmmSimpleRouter.sol line 72-80
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

`msg.sender` of the pool's `swap` call is therefore the **router contract**, not the user. The allowlist check becomes `allowedSwapper[pool][router]`.

**Two broken invariants arise simultaneously:**

1. **Bypass (router allowlisted):** If the pool admin allowlists the router — the natural action to enable router-mediated swaps for allowlisted users — every unprivileged user can bypass the allowlist by routing through the router. The extension cannot distinguish between allowlisted and non-allowlisted users once the router is the `sender`.

2. **Broken functionality (router not allowlisted):** If the router is not allowlisted, allowlisted users who attempt to swap through the router receive `NotAllowedToSwap`, making the router unusable for any curated pool. There is no configuration that simultaneously allows allowlisted users to use the router and blocks non-allowlisted users.

The root cause is the same as the external bug class: two distinct roles — the **immediate caller of the pool** (the router) and the **economic actor** (the actual user) — are conflated into a single `sender` value, causing the guard to check the wrong identity.

---

### Impact Explanation

A curated pool's swap allowlist is completely defeated for any user who routes through `MetricOmmSimpleRouter`. Non-allowlisted users gain full swap access to a pool the admin intended to restrict. Depending on the pool's purpose (KYC compliance, private liquidity, regulated access), this constitutes an admin-boundary break where an unprivileged path bypasses a configured access control, with direct fund-flow consequences (unauthorized swaps execute against pool liquidity).

---

### Likelihood Explanation

Medium. The bypass requires the pool admin to allowlist the router. However, this is the expected operational step for any curated pool that wants to support the standard periphery swap path. The admin has no mechanism to allowlist the router for specific users only — the allowlist entry is binary per address. Any admin who enables router-mediated swaps for their allowlisted users inadvertently opens the pool to all users.

---

### Recommendation

The `beforeSwap` hook must check the **actual user**, not the immediate caller of the pool. Two sound approaches:

1. **Forward the originating user through `extensionData`:** The router encodes `msg.sender` into `extensionData`; the extension decodes and checks that address. This requires a coordinated convention between the router and the extension.

2. **Separate `sender` from `caller` in the hook signature:** The pool could pass both `msg.sender` (the immediate caller) and a separately tracked originating user. Extensions that need to gate the economic actor use the originating user; extensions that need to gate the immediate caller use `msg.sender`.

Until fixed, pool admins should not deploy `SwapAllowlistExtension` on pools that are also expected to be reachable through `MetricOmmSimpleRouter`.

---

### Proof of Concept

```
Setup:
  - Pool P deployed with SwapAllowlistExtension E
  - Pool admin allowlists Alice (KYC'd user): allowedSwapper[P][Alice] = true
  - Pool admin allowlists Router R to enable router-mediated swaps: allowedSwapper[P][R] = true

Attack (Bob, not allowlisted):
  1. Bob calls MetricOmmSimpleRouter.exactInputSingle({pool: P, ...})
  2. Router calls P.swap(recipient, ...) → msg.sender of pool.swap = Router R
  3. Pool calls _beforeSwap(sender=R, ...)
  4. Extension checks allowedSwapper[P][R] → true
  5. Bob's swap executes successfully despite Bob not being on the allowlist

Result:
  - Bob swaps in a pool restricted to KYC'd users
  - allowedSwapper[P][Bob] was never set; the check never touched Bob's address
``` [1](#0-0) [2](#0-1) [3](#0-2) [4](#0-3) [5](#0-4)

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

**File:** metric-periphery/contracts/extensions/base/BaseMetricExtension.sol (L19-24)
```text
  modifier onlyPool() {
    if (!IMetricOmmPoolFactory(FACTORY).isPool(msg.sender)) {
      revert OnlyPool(msg.sender, FACTORY);
    }
    _;
  }
```
