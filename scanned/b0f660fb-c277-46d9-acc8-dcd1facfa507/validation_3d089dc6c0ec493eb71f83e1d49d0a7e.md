### Title
`SwapAllowlistExtension` Checks Router Address as Swapper Identity, Allowing Full Allowlist Bypass via `MetricOmmSimpleRouter` — (`metric-periphery/contracts/extensions/SwapAllowlistExtension.sol`)

---

### Summary

`SwapAllowlistExtension.beforeSwap` gates swaps by checking `sender`, which the pool sets to `msg.sender` of the `pool.swap()` call. When a user routes through `MetricOmmSimpleRouter`, `sender` is the **router address**, not the actual end user. If the pool admin allowlists the router (the only way to permit router-mediated swaps on a curated pool), every unprivileged user can bypass the allowlist by routing through the router.

---

### Finding Description

**Call chain for a router-mediated swap:**

```
user → MetricOmmSimpleRouter.exactInputSingle(params)
     → pool.swap(params.recipient, ...)          // msg.sender = router
     → _beforeSwap(msg.sender=router, recipient, ...)
     → SwapAllowlistExtension.beforeSwap(sender=router, ...)
```

Inside `MetricOmmPool.swap`, the pool passes `msg.sender` as `sender` to the extension: [1](#0-0) 

`ExtensionCalling._beforeSwap` forwards that value unchanged: [2](#0-1) 

`SwapAllowlistExtension.beforeSwap` then checks `allowedSwapper[msg.sender][sender]`, where `msg.sender` is the pool and `sender` is the router: [3](#0-2) 

Meanwhile, the router passes `params.recipient` (user-controlled) as the pool's `recipient` argument and stores the real payer only in transient callback context — the extension never sees the actual end user: [4](#0-3) 

A pool admin who wants curated users to be able to use the standard periphery **must** call `setAllowedToSwap(pool, router, true)`. Once the router is allowlisted, the check `allowedSwapper[pool][router]` returns `true` for every caller, regardless of who the actual end user is.

The same issue applies to `exactInput` multi-hop paths and `exactOutputSingle`/`exactOutput`, because in every case the pool's `msg.sender` is the router. [5](#0-4) 

---

### Impact Explanation

A pool admin who deploys a curated pool (e.g., KYC-gated, institutional-only, or market-maker-restricted) and allowlists the router to give approved users a standard UX inadvertently opens the pool to **all** users. Any non-allowlisted address can execute swaps against the pool by routing through `MetricOmmSimpleRouter`. The allowlist extension provides zero protection in this configuration. If the pool is designed for specific market makers at tight oracle spreads, unrestricted access exposes LP principal to arbitrage and MEV extraction that the curation was meant to prevent.

---

### Likelihood Explanation

The scenario is directly reachable by any unprivileged user with no special setup. The trigger is simply calling `MetricOmmSimpleRouter.exactInputSingle` on a pool whose admin has allowlisted the router. Allowlisting the router is the only way to let approved users use the standard periphery, so any pool admin who wants both curation and router support will inevitably create this configuration. No privileged action by the attacker is required.

---

### Recommendation

`SwapAllowlistExtension.beforeSwap` should gate on the **economically responsible actor** — the address that pays for the swap — rather than the immediate `msg.sender` of the pool call. Two viable approaches:

1. **Check `recipient` instead of `sender`** if the pool's design intent is to gate who receives output (less common).
2. **Require the actual payer identity to be forwarded in `extensionData`** and have the router encode `msg.sender` there; the extension decodes and verifies it. This requires a coordinated router + extension update.
3. **Document that the router must never be allowlisted** and provide a router variant that enforces its own per-user allowlist check before calling the pool.

---

### Proof of Concept

```solidity
// Setup: pool admin deploys curated pool with SwapAllowlistExtension
// Admin allowlists the router so approved users can use standard periphery
extension.setAllowedToSwap(address(pool), address(router), true);

// Attacker: non-allowlisted address
address attacker = makeAddr("attacker");
// attacker is NOT in allowedSwapper[pool][attacker]

// Attacker routes through the router — sender seen by extension = router (allowlisted)
vm.prank(attacker);
router.exactInputSingle(
    IMetricOmmSimpleRouter.ExactInputSingleParams({
        pool: address(pool),
        recipient: attacker,
        zeroForOne: true,
        amountIn: 1000,
        amountOutMinimum: 0,
        priceLimitX64: 0,
        deadline: block.timestamp + 1,
        extensionData: ""
    })
);
// Swap succeeds — allowlist bypassed
```

The extension checks `allowedSwapper[pool][router] == true` and passes, even though `attacker` was never allowlisted. [6](#0-5) [7](#0-6)

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

**File:** metric-periphery/contracts/MetricOmmSimpleRouter.sol (L71-80)
```text
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
```

**File:** metric-periphery/contracts/MetricOmmSimpleRouter.sol (L99-118)
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

      int128 amountInActual = MetricOmmSwapResults.extractAmountIn(zeroForOne, amount0Delta, amount1Delta);
      if (amountInActual < amount) revert InvalidInputAmountAtHop(uint8(i), amountInActual, amount);

      amount = MetricOmmSwapResults.extractAmountOut(zeroForOne, amount0Delta, amount1Delta);
    }
```
