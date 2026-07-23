### Title
`SwapAllowlistExtension` Checks the Router's Address Instead of the Actual End-User, Enabling Allowlist Bypass Through `MetricOmmSimpleRouter` - (`metric-periphery/contracts/extensions/SwapAllowlistExtension.sol`)

---

### Summary

`SwapAllowlistExtension.beforeSwap` gates swaps by checking `sender`, which is the `msg.sender` of the pool's `swap` call. When a user routes through `MetricOmmSimpleRouter`, `sender` is the **router address**, not the actual end-user. A pool admin who allowlists the router (to enable router-based swaps for their curated users) inadvertently opens the gate to every user who routes through it, because the extension cannot distinguish between allowlisted and non-allowlisted users once the router is the direct caller.

---

### Finding Description

The call chain for a router-mediated swap is:

```
user → MetricOmmSimpleRouter.exactInputSingle()
     → pool.swap(recipient, zeroForOne, amount, priceLimit, "", extensionData)
          msg.sender = router
     → ExtensionCalling._beforeSwap(msg.sender=router, recipient, ...)
     → SwapAllowlistExtension.beforeSwap(sender=router, ...)
          checks: allowedSwapper[pool][router]   ← router, not the user
```

`MetricOmmPool.swap` passes `msg.sender` as the `sender` argument to `_beforeSwap`: [1](#0-0) 

`ExtensionCalling._beforeSwap` forwards that value unchanged to every configured extension: [2](#0-1) 

`SwapAllowlistExtension.beforeSwap` then checks `allowedSwapper[msg.sender][sender]`, where `msg.sender` is the pool and `sender` is whatever the pool received as its own `msg.sender` — the router: [3](#0-2) 

The router calls `pool.swap` directly with no mechanism to forward the original caller's identity: [4](#0-3) 

The asymmetry with `DepositAllowlistExtension` is the clearest indicator of the bug. The deposit extension correctly checks `owner` — the actual position recipient — not `sender` (the direct caller): [5](#0-4) 

---

### Impact Explanation

**Scenario A — Allowlist bypass (High):**
A pool admin deploys a curated pool with `SwapAllowlistExtension`, allowlists specific users, and also allowlists the router so those users can swap via the standard periphery path. Because the extension only sees `sender = router`, every user who calls through the router is treated as the router. Any non-allowlisted user can bypass the curation gate entirely by routing through `MetricOmmSimpleRouter`, trading on a pool that was designed to exclude them.

**Scenario B — Broken core functionality (Medium):**
If the pool admin does not allowlist the router, allowlisted users who attempt to swap through the router are blocked (`NotAllowedToSwap`), even though they are individually permitted. The router — the primary supported periphery path — becomes unusable for any allowlisted pool, breaking core swap functionality.

Both scenarios are direct consequences of the wrong actor being checked. Scenario A matches the "admin-boundary break / allowlist bypass by an unprivileged path" impact gate; Scenario B matches "broken core pool functionality."

---

### Likelihood Explanation

The `MetricOmmSimpleRouter` is the primary supported swap path for end-users. Any pool that deploys `SwapAllowlistExtension` and expects router-based swaps to work will encounter one of the two failure modes above. The trigger requires no special privileges: any user can call the router. The pool admin's only escape is to either never allowlist the router (breaking router usage for everyone) or set `allowAllSwappers = true` (defeating the allowlist entirely).

---

### Recommendation

The extension must check the economically relevant actor — the actual end-user — not the direct pool caller. The cleanest fix mirrors how `DepositAllowlistExtension` handles the operator pattern: the pool already passes both `sender` (direct caller) and `recipient` to the hook. For router flows, the real user is the `recipient` when swapping for themselves, but this is not reliable in general.

The robust fix is to have the router encode the original caller's address in `extensionData` and have `SwapAllowlistExtension` decode it when `sender` is a known router, or to add a dedicated `originalCaller` field to the `beforeSwap` hook signature. At minimum, document that `sender` is the direct pool caller and that router-mediated swaps will present the router address, so pool admins can make an informed allowlist decision.

---

### Proof of Concept

```solidity
// Setup:
// 1. Pool admin deploys pool with SwapAllowlistExtension
// 2. Pool admin allowlists alice (legitimate user)
// 3. Pool admin allowlists router (to enable router-based swaps for alice)
//    swapExtension.setAllowedToSwap(pool, router, true);
//    swapExtension.setAllowedToSwap(pool, alice, true);

// Attack:
// bob is NOT allowlisted
// bob calls router.exactInputSingle with the curated pool
// router calls pool.swap(...) — msg.sender = router
// beforeSwap receives sender = router
// allowedSwapper[pool][router] == true  ← bob bypasses the allowlist
// swap executes for bob on a pool that was designed to exclude him

vm.prank(bob); // bob is not in allowedSwapper
router.exactInputSingle(
    IMetricOmmSimpleRouter.ExactInputSingleParams({
        pool: curatedPool,
        recipient: bob,
        zeroForOne: true,
        amountIn: 1_000e6,
        amountOutMinimum: 0,
        priceLimitX64: 0,
        extensionData: "",
        deadline: block.timestamp + 1
    })
);
// succeeds — allowlist bypassed because extension saw sender = router (allowlisted), not bob
``` [6](#0-5) [1](#0-0) [4](#0-3)

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

**File:** metric-periphery/contracts/MetricOmmSimpleRouter.sol (L71-85)
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
    int128 out = MetricOmmSwapResults.extractAmountOut(params.zeroForOne, amount0Delta, amount1Delta);
    amountOut = MetricOmmSwapInputs.int128ToUint128(out);
    if (amountOut < params.amountOutMinimum) revert InsufficientOutput(amountOut, params.amountOutMinimum);

    _clearExpectedCallbackPool();
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
