### Title
`SwapAllowlistExtension` Allowlist Bypassed via Router: `sender` Is the Router, Not the User — (`metric-periphery/contracts/extensions/SwapAllowlistExtension.sol`)

---

### Summary

`SwapAllowlistExtension.beforeSwap` gates swaps by checking the `sender` argument against a per-pool allowlist. However, `sender` is `msg.sender` of `MetricOmmPool.swap`, which is the **router contract** when a user routes through `MetricOmmSimpleRouter`. If the pool admin allowlists the router (the natural step to enable router-mediated swaps), every unpermissioned user can bypass the per-user allowlist by calling any `exact*` function on the router.

---

### Finding Description

`SwapAllowlistExtension.beforeSwap` performs the following check:

```solidity
// SwapAllowlistExtension.sol:37-39
if (!allowAllSwappers[msg.sender] && !allowedSwapper[msg.sender][sender]) {
    revert IMetricOmmPoolActions.NotAllowedToSwap();
}
```

`msg.sender` here is the pool (the extension caller). `sender` is the first argument forwarded by the pool, which is `msg.sender` of `pool.swap`:

```solidity
// MetricOmmPool.sol:230-240
_beforeSwap(
    msg.sender,   // ← this is the router when called via router
    recipient,
    ...
);
```

When a user calls `MetricOmmSimpleRouter.exactInputSingle`, the router calls `pool.swap(...)` directly:

```solidity
// MetricOmmSimpleRouter.sol:72-80
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

The pool receives `msg.sender = router`. It passes `sender = router` to the extension. The extension therefore evaluates `allowedSwapper[pool][router]`, not `allowedSwapper[pool][original_user]`.

The pool admin who wants to allow router-mediated swaps for their allowlisted users must add the router to the allowlist. Once `allowedSwapper[pool][router] = true`, the check passes for **every** user who routes through the router, regardless of whether that user is individually allowlisted. The original user's identity is never consulted.

The same bypass applies to `exactInput`, `exactOutputSingle`, and `exactOutput` — all of which call `pool.swap` with `msg.sender = router`.

---

### Impact Explanation

The `SwapAllowlistExtension` is the protocol's mechanism for restricting which addresses may trade against a pool. A pool admin who deploys this extension intends to create a permissioned pool. Once the router is allowlisted (required for normal UX), the allowlist is completely ineffective: any address can swap by routing through `MetricOmmSimpleRouter`. Unpermissioned users gain full swap access, draining or manipulating pool reserves in ways the admin explicitly intended to prevent. This is a direct loss of the access-control invariant with fund-impacting consequences (unauthorized swaps alter pool balances and LP claims).

---

### Likelihood Explanation

The router is a core periphery contract. Any pool admin who wants their allowlisted users to be able to use the standard router must add the router to the allowlist. This is the expected operational path. The bypass is therefore reachable in every realistic deployment of `SwapAllowlistExtension` that also supports router-mediated trading.

---

### Recommendation

The extension must gate the **original user**, not the intermediary. Two complementary fixes:

1. **Pass the original user through `extensionData`**: The router encodes `msg.sender` into `extensionData`; the extension decodes and checks it. This requires a coordinated change to the router and extension.

2. **Check `sender` only when `sender` is not a known router**: Maintain a registry of trusted routers in the extension; when `sender` is a router, require the original user to be encoded in `extensionData` and check that address instead.

3. **Alternatively, remove router allowlisting and require users to call the pool directly** for allowlisted pools — but this breaks standard UX.

---

### Proof of Concept

```
Setup:
  - Deploy pool with SwapAllowlistExtension
  - Pool admin allowlists Alice: allowedSwapper[pool][alice] = true
  - Pool admin allowlists router: allowedSwapper[pool][router] = true
    (required so Alice can use the router)

Attack (Bob, not allowlisted):
  1. Bob calls MetricOmmSimpleRouter.exactInputSingle({pool: pool, ...})
  2. Router calls pool.swap(...) — msg.sender = router
  3. Pool calls _beforeSwap(sender=router, ...)
  4. Extension checks allowedSwapper[pool][router] → true → passes
  5. Bob's swap executes successfully despite not being on the allowlist
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
