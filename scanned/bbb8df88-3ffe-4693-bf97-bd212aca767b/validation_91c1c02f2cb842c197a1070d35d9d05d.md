### Title
SwapAllowlistExtension Gates on Router Address Instead of End User, Allowing Any User to Bypass the Swap Allowlist — (`metric-periphery/contracts/extensions/SwapAllowlistExtension.sol`)

---

### Summary

`SwapAllowlistExtension.beforeSwap` checks the `sender` argument forwarded by the pool, which equals `msg.sender` of `MetricOmmPool.swap`. When a swap is routed through `MetricOmmSimpleRouter`, `sender` is the **router contract address**, not the originating user. If the pool admin allowlists the router (required for any router-mediated swap to succeed on the curated pool), every unpermissioned user can bypass the individual allowlist by routing through the public router.

---

### Finding Description

`SwapAllowlistExtension.beforeSwap` performs the following check:

```solidity
// metric-periphery/contracts/extensions/SwapAllowlistExtension.sol L37-38
if (!allowAllSwappers[msg.sender] && !allowedSwapper[msg.sender][sender]) {
    revert IMetricOmmPoolActions.NotAllowedToSwap();
}
```

`msg.sender` here is the pool (the extension is called by the pool). `sender` is the first argument, which `MetricOmmPool.swap` sets to its own `msg.sender`:

```solidity
// metric-core/contracts/MetricOmmPool.sol L230-240
_beforeSwap(
    msg.sender,   // ← whoever called pool.swap()
    recipient,
    ...
);
```

When a user calls the pool directly, `sender = user` and `allowedSwapper[pool][user]` is checked — correct.

When a user calls through `MetricOmmSimpleRouter.exactInputSingle`, the router calls `pool.swap(...)` directly:

```solidity
// metric-periphery/contracts/MetricOmmSimpleRouter.sol L72-80
IMetricOmmPoolActions(params.pool).swap(
    params.recipient,
    params.zeroForOne,
    MetricOmmSwapInputs.asAmountSpecifiedIn(params.amountIn),
    priceLimitX64,
    "",
    params.extensionData
);
```

The pool's `msg.sender` is now the **router**, so `sender = router`. The extension checks `allowedSwapper[pool][router]`. For any router-mediated swap to succeed on the allowlisted pool, the admin must allowlist the router address. Once the router is allowlisted, the check `allowedSwapper[pool][router]` passes for **every user** who routes through it, regardless of whether that user is individually permitted.

The same structural flaw exists in `DepositAllowlistExtension.beforeAddLiquidity`, which checks `owner` (a caller-supplied argument) rather than the actual payer/depositor, but the swap path is the more directly exploitable vector because the router is a standard, permissionless periphery entry point.

---

### Impact Explanation

A pool admin deploys a curated pool with `SwapAllowlistExtension` to restrict trading to a known set of counterparties (e.g., KYC'd addresses, whitelisted market makers). The admin must allowlist the router to support standard user flows. Once the router is allowlisted, any address — including those explicitly not in the allowlist — can call `MetricOmmSimpleRouter.exactInputSingle` or `exactInput` and execute swaps against the curated pool. The allowlist is completely bypassed. This constitutes a direct admin-boundary break and broken core pool functionality: the pool's primary access-control invariant fails open for all router-mediated swaps.

**Severity: High** — the allowlist protection is silently nullified for the standard public swap path.

---

### Likelihood Explanation

- `MetricOmmSimpleRouter` is the standard, documented periphery entry point for swaps.
- Any pool admin who wants users to swap via the router (the expected UX) must allowlist the router.
- No special preconditions, privileged access, or non-standard tokens are required. Any user can call the router.
- The bypass is unconditional once the router is allowlisted: it requires no timing, no flash loan, and no multi-step setup.

---

### Recommendation

The extension must gate on the **originating user**, not the immediate pool caller. Two complementary fixes:

1. **Pass the original user through the router**: `MetricOmmSimpleRouter` should forward `msg.sender` as part of `extensionData` (signed or via a trusted forwarder pattern), and `SwapAllowlistExtension` should decode and verify it when `sender` is a known router.

2. **Check `sender` only when it is not a trusted router, and check the decoded user otherwise**: The extension can maintain a registry of trusted routers; when `sender` is a trusted router, it reads the actual user from `extensionData` and checks that address against the allowlist.

The simplest safe fix is to **not allowlist the router** and require allowlisted users to call the pool directly — but this breaks the standard UX and is not a code-level fix.

---

### Proof of Concept

```
Setup:
  - Deploy pool with SwapAllowlistExtension.
  - Admin allowlists only `alice` and the router (router must be allowlisted for alice to use it).
  - `bob` is NOT allowlisted.

Attack:
  1. bob calls MetricOmmSimpleRouter.exactInputSingle({pool: pool, ...}).
  2. Router calls pool.swap(recipient, ...) — msg.sender to pool = router.
  3. Pool calls _beforeSwap(router, ...) → extension receives sender = router.
  4. Extension checks allowedSwapper[pool][router] → TRUE (admin allowlisted router).
  5. Swap executes. bob has bypassed the allowlist.

Expected: revert NotAllowedToSwap.
Actual:   swap succeeds.
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
