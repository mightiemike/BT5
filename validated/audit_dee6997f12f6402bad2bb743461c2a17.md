### Title
SwapAllowlistExtension Checks Router Address Instead of Actual User, Enabling Allowlist Bypass via MetricOmmSimpleRouter — (`metric-periphery/contracts/extensions/SwapAllowlistExtension.sol`)

---

### Summary

`SwapAllowlistExtension.beforeSwap` gates swaps by checking the `sender` argument, which the pool sets to `msg.sender` (the direct caller of `pool.swap`). When a user routes through `MetricOmmSimpleRouter`, the pool's `msg.sender` is the router contract, not the user. If the pool admin allowlists the router to support router-mediated swaps for legitimate users, every unprivileged user can bypass the allowlist by routing through the same public router.

---

### Finding Description

`MetricOmmPool.swap` passes `msg.sender` as the `sender` argument to `_beforeSwap`: [1](#0-0) 

`ExtensionCalling._beforeSwap` forwards that value unchanged to every configured extension: [2](#0-1) 

`SwapAllowlistExtension.beforeSwap` then checks `allowedSwapper[msg.sender][sender]`, where `msg.sender` is the pool and `sender` is whoever called `pool.swap`: [3](#0-2) 

`MetricOmmSimpleRouter.exactInputSingle` calls `pool.swap` directly, making the router the pool's `msg.sender`: [4](#0-3) 

The same pattern applies to `exactInput`, `exactOutputSingle`, and `exactOutput`. [5](#0-4) 

The result is a forced dilemma for any pool admin who deploys a curated pool with `SwapAllowlistExtension`:

- **Do not allowlist the router** → allowlisted users cannot use the router at all; they must call `pool.swap` directly.
- **Allowlist the router** → every user, including those explicitly excluded from the allowlist, can bypass the gate by routing through the public router.

There is no configuration that simultaneously allows allowlisted users to use the router and blocks non-allowlisted users from doing the same.

---

### Impact Explanation

Any user excluded from the swap allowlist can trade on a curated pool by calling `MetricOmmSimpleRouter.exactInputSingle` (or any other router entry point). The pool receives the swap, the extension sees `sender = router`, and if the router is allowlisted, the check passes. The unauthorized user receives pool output tokens at the oracle-derived price, bypassing whatever curation policy the pool admin intended to enforce (e.g., KYC compliance, counterparty restrictions, or exclusive LP arrangements). This is a direct policy bypass with fund-impacting consequences: the unauthorized trader extracts value from the pool at prices the admin reserved for approved counterparties.

---

### Likelihood Explanation

The router is a standard, documented periphery contract. Pool admins who want to support router-mediated swaps for their allowlisted users have no choice but to allowlist the router address. This is a natural and expected configuration step. Once the router is allowlisted, the bypass is trivially reachable by any unprivileged user with no special setup.

---

### Recommendation

The extension must check the economically relevant actor — the end user — not the intermediary contract. Two approaches:

1. **Pass the original initiator through the router**: Have `MetricOmmSimpleRouter` encode the original `msg.sender` in `extensionData` and have `SwapAllowlistExtension.beforeSwap` decode and check that value instead of (or in addition to) `sender`.

2. **Check both sender and recipient**: Gate on the `recipient` parameter when `sender` is a known router, or require the extension to verify both the direct caller and the ultimate beneficiary.

The cleanest fix is approach 1: the router appends `abi.encode(msg.sender)` to `extensionData` before forwarding to the pool, and the extension reads the original initiator from `extensionData` when `sender` is a recognized router address.

---

### Proof of Concept

```
Setup:
  pool configured with SwapAllowlistExtension
  admin allowlists Alice (direct user) and the router (to support router swaps)
  admin does NOT allowlist Charlie

Attack:
  Charlie calls MetricOmmSimpleRouter.exactInputSingle({
    pool: curated_pool,
    recipient: charlie,
    zeroForOne: true,
    amountIn: X,
    ...
  })

Execution trace:
  router.exactInputSingle()
    → pool.swap(recipient=charlie, ...) [msg.sender = router]
      → _beforeSwap(sender=router, ...)
        → SwapAllowlistExtension.beforeSwap(sender=router, ...)
          → allowedSwapper[pool][router] == true  ← passes
      → swap executes, charlie receives tokens
      → router pays pool via callback

Result:
  Charlie, who is not on the allowlist, successfully swaps on the curated pool.
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

**File:** metric-periphery/contracts/MetricOmmSimpleRouter.sol (L103-112)
```text
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
