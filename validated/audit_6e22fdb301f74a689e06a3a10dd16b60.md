### Title
SwapAllowlistExtension Swap Guard Bypassed via MetricOmmSimpleRouter — (`metric-periphery/contracts/extensions/SwapAllowlistExtension.sol`)

---

### Summary

`SwapAllowlistExtension.beforeSwap` gates swaps by checking `allowedSwapper[pool][sender]`, where `sender` is the immediate `msg.sender` of `pool.swap()`. When `MetricOmmSimpleRouter` is used, the router is the immediate caller of `pool.swap()`, so the extension checks the router's address — not the end user's address. Any pool admin who allowlists the router to enable router-mediated swaps for their permitted users simultaneously opens the gate to every user on the network, completely defeating the per-user allowlist.

---

### Finding Description

`MetricOmmPool.swap` passes `msg.sender` as the `sender` argument to `_beforeSwap`: [1](#0-0) 

`ExtensionCalling._beforeSwap` forwards that value unchanged to the extension: [2](#0-1) 

`SwapAllowlistExtension.beforeSwap` then checks `allowedSwapper[msg.sender][sender]`, where `msg.sender` is the pool and `sender` is the first argument — the immediate caller of `pool.swap()`: [3](#0-2) 

`MetricOmmSimpleRouter.exactInputSingle` (and `exactInput`, `exactOutputSingle`, `exactOutput`) calls `pool.swap()` directly, making the router the `msg.sender` of that call: [4](#0-3) 

The extension therefore checks `allowedSwapper[pool][router]`, not `allowedSwapper[pool][end_user]`.

**The dilemma this creates for the pool admin:**

| Admin action | Effect |
|---|---|
| Router **not** allowlisted | Allowlisted users cannot use the router at all — only direct `pool.swap()` calls work |
| Router **allowlisted** | Every user on the network can bypass the per-user allowlist by routing through the router |

There is no configuration that simultaneously allows allowlisted users to use the router and blocks non-allowlisted users from doing the same. The guard is structurally misapplied: it checks the intermediary, not the economic actor.

---

### Impact Explanation

A pool configured with `SwapAllowlistExtension` is intended to restrict trading to a specific set of counterparties (e.g., KYC-verified addresses, institutional market makers, or whitelisted protocols). Once the pool admin allowlists the router — a natural operational step to let permitted users access the standard periphery — the allowlist becomes a no-op. Any address can call `router.exactInputSingle()` and execute a swap against the restricted pool. This constitutes an admin-boundary break: an unprivileged path bypasses an admin-configured access control guard, allowing unauthorized parties to interact with a pool that was explicitly designed to exclude them.

---

### Likelihood Explanation

The bypass is reachable by any user with no special privileges. The only prerequisite is that the pool admin has allowlisted the router, which is the expected operational step for any pool that wants its permitted users to use the standard periphery. The router is a public, permissionless contract. No malicious setup is required.

---

### Recommendation

The allowlist must gate the end user, not the immediate caller of `pool.swap()`. Two sound approaches:

1. **Check `recipient` instead of (or in addition to) `sender`** — for router-mediated swaps the recipient is the end user's address. However, `recipient` can also be set to an intermediate contract in multi-hop paths, so this alone is insufficient.

2. **Require the end user to pass a signed credential in `extensionData`** — the extension decodes a signature from `extensionData` that proves the end user is allowlisted, regardless of which intermediary called `pool.swap()`. This is the most robust approach.

3. **Do not allowlist the router; instead require allowlisted users to call `pool.swap()` directly** — this is operationally restrictive but closes the bypass without code changes.

---

### Proof of Concept

```
Setup:
  pool = MetricOmmPool with SwapAllowlistExtension in beforeSwap order
  allowedSwapper[pool][alice] = true          // alice is the only permitted swapper
  allowedSwapper[pool][router] = true         // admin allowlists router so alice can use it

Attack (executed by bob, who is NOT allowlisted):
  bob calls router.exactInputSingle({
      pool: pool,
      tokenIn: token0,
      tokenOut: token1,
      zeroForOne: true,
      amountIn: X,
      ...
  })

  router calls pool.swap(recipient, true, X, ...) with msg.sender = router
  pool calls _beforeSwap(sender=router, ...)
  extension checks allowedSwapper[pool][router] → true → swap proceeds

Result:
  bob successfully swaps against the restricted pool
  SwapAllowlistExtension.NotAllowedToSwap is never triggered
  The per-user allowlist is completely bypassed
``` [5](#0-4) [6](#0-5) [1](#0-0)

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
