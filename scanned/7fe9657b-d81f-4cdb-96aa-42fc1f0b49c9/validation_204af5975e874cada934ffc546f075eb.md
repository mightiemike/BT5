### Title
SwapAllowlistExtension gates the router address instead of the actual user, allowing full allowlist bypass via MetricOmmSimpleRouter — (File: metric-periphery/contracts/extensions/SwapAllowlistExtension.sol)

---

### Summary

`SwapAllowlistExtension.beforeSwap` checks `allowedSwapper[pool][sender]` where `sender` is `msg.sender` of the pool's `swap()` call. When a user routes through `MetricOmmSimpleRouter`, `sender` is the **router address**, not the actual user. If the pool admin allowlists the router to enable router-based swaps, every unprivileged user can bypass the per-user allowlist entirely.

---

### Finding Description

**Actor binding in the pool's swap path:**

`MetricOmmPool.swap()` passes `msg.sender` as the `sender` argument to `_beforeSwap`: [1](#0-0) 

`_beforeSwap` forwards that value unchanged to every configured extension: [2](#0-1) 

**What the router passes to the pool:**

`MetricOmmSimpleRouter.exactInputSingle` calls `pool.swap(params.recipient, ...)` with `msg.sender = router`. The actual user who called the router is stored only in transient storage for the payment callback and is **never forwarded to the pool or any extension**: [3](#0-2) 

**What the extension checks:**

`SwapAllowlistExtension.beforeSwap` receives `sender = router` and checks `allowedSwapper[pool][router]`: [4](#0-3) 

**The asymmetry with the deposit path:**

`DepositAllowlistExtension.beforeAddLiquidity` ignores `sender` (the immediate caller) and checks `owner` — the actual position owner explicitly passed through `addLiquidity`: [5](#0-4) 

`addLiquidity` accepts an explicit `owner` parameter that survives the router/adder hop, so the deposit allowlist correctly gates the economic actor. `swap` has no equivalent explicit-user parameter; the only identity available to the extension is `msg.sender` of the pool call, which is the router.

**Resulting invariant break:**

| Path | Actor checked by allowlist | Correct? |
|---|---|---|
| Direct `pool.addLiquidity(owner, ...)` | `owner` | ✓ |
| `LiquidityAdder.addLiquidity(pool, owner, ...)` | `owner` | ✓ |
| Direct `pool.swap(...)` | actual user | ✓ |
| `Router.exactInputSingle(...)` → `pool.swap(...)` | **router address** | ✗ |

---

### Impact Explanation

A pool admin who deploys a curated pool with `SwapAllowlistExtension` and allowlists the router (a natural action to let allowlisted users trade via the standard periphery) inadvertently opens the pool to **all** users. The check `allowedSwapper[pool][router] = true` passes for every caller of the router regardless of their individual allowlist status. Non-allowlisted users can execute swaps against the pool's liquidity, violating the curation policy and potentially extracting value the pool admin intended to restrict.

Even without the router being allowlisted, allowlisted users are silently forced to bypass the standard periphery and call the pool directly — a broken core flow.

---

### Likelihood Explanation

The trigger is a pool admin allowlisting the router address, which is a reasonable and expected operational step for any curated pool that wants to support the standard periphery. The admin has no on-chain signal that doing so collapses per-user enforcement to a single binary gate. Any non-allowlisted user can then exploit this by calling `MetricOmmSimpleRouter.exactInputSingle` or `exactInput` with no special privileges.

---

### Recommendation

1. **Pass the actual user through the swap path.** Add an explicit `swapper` parameter to `pool.swap()` (analogous to `owner` in `addLiquidity`) and have the router forward `msg.sender` in that field. The extension then checks `allowedSwapper[pool][swapper]`.

2. **Alternatively**, have the router encode the actual user in `extensionData` under a well-known ABI layout and have `SwapAllowlistExtension` decode and verify it — though this is weaker because it relies on the router being the only entry point.

3. **At minimum**, document that `SwapAllowlistExtension` is incompatible with `MetricOmmSimpleRouter` and that allowlisting the router disables per-user enforcement.

---

### Proof of Concept

```
Setup:
  pool configured with SwapAllowlistExtension
  allowedSwapper[pool][userA]  = true   // intended: only userA may swap
  allowedSwapper[pool][router] = true   // admin adds this to let userA use the router

Attack:
  userB (not allowlisted) calls:
    MetricOmmSimpleRouter.exactInputSingle({
      pool:      pool,
      recipient: userB,
      ...
    })

  Router executes:
    pool.swap(userB, ...)   // msg.sender = router

  Pool calls:
    _beforeSwap(sender=router, ...)

  SwapAllowlistExtension.beforeSwap:
    allowedSwapper[pool][router] == true  →  passes, no revert

  userB receives output tokens from the curated pool.
  Per-user allowlist is completely bypassed.
``` [6](#0-5) [7](#0-6) [8](#0-7)

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
