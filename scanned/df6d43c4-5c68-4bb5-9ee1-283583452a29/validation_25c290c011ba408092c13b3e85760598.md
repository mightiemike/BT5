### Title
`SwapAllowlistExtension` Checks Router Address Instead of Original EOA, Allowing Any User to Bypass the Swap Allowlist via `MetricOmmSimpleRouter` - (`metric-periphery/contracts/extensions/SwapAllowlistExtension.sol`)

---

### Summary

`SwapAllowlistExtension.beforeSwap` gates swaps using the `sender` argument, which the pool sets to `msg.sender` of the `pool.swap()` call. When a user routes through `MetricOmmSimpleRouter`, `msg.sender` at the pool is the **router address**, not the original EOA. If the pool admin allowlists the router (the only way to let allowlisted users trade through the router), every non-allowlisted user can also bypass the guard by routing through the same public router.

---

### Finding Description

`MetricOmmPool.swap()` passes `msg.sender` as the `sender` argument to `_beforeSwap`: [1](#0-0) 

`ExtensionCalling._beforeSwap` forwards that value unchanged to every configured extension: [2](#0-1) 

`SwapAllowlistExtension.beforeSwap` then checks `allowedSwapper[msg.sender][sender]`, where `msg.sender` is the pool and `sender` is the direct caller of `pool.swap()`: [3](#0-2) 

When a user calls `MetricOmmSimpleRouter.exactInputSingle()`, the router becomes the direct caller of `pool.swap()`: [4](#0-3) 

The extension therefore evaluates `allowedSwapper[pool][router]` — the router's allowlist entry — not the originating EOA's entry. A pool admin who wants allowlisted EOAs to be able to use the router must add the router address to the allowlist. Once the router is allowlisted, **every** user who calls any `exact*` function on the router passes the guard, regardless of whether their own address is on the allowlist.

---

### Impact Explanation

A curated pool deploying `SwapAllowlistExtension` to restrict trading to a specific set of addresses (e.g., KYC-verified counterparties, institutional LPs, or whitelisted market makers) loses that restriction entirely for any user who routes through `MetricOmmSimpleRouter`. The non-allowlisted user receives the full swap output from the pool's liquidity, which constitutes a direct policy bypass with fund-flow consequences: LP assets are consumed by an actor the pool was explicitly configured to exclude.

---

### Likelihood Explanation

The scenario is reachable by any unprivileged user with no special setup:

1. The pool admin deploys a pool with `SwapAllowlistExtension` and allowlists specific EOAs.
2. To let those EOAs use the router (the standard periphery path), the admin also calls `setAllowedToSwap(pool, router, true)`.
3. Any non-allowlisted EOA calls `MetricOmmSimpleRouter.exactInputSingle(pool, ...)`.
4. The router calls `pool.swap()` with `msg.sender = router`.
5. The extension checks `allowedSwapper[pool][router] == true` → passes.
6. The swap executes and the non-allowlisted user receives pool output.

Step 2 is the only prerequisite, and it is the natural action any pool admin would take to make the allowlist usable with the standard router.

---

### Recommendation

The extension must gate the **original EOA**, not the direct caller of `pool.swap()`. Two approaches:

1. **Pass the original sender through the router.** Have `MetricOmmSimpleRouter` encode the originating `msg.sender` into `extensionData` and have `SwapAllowlistExtension.beforeSwap` decode and check it. This requires a convention between the router and the extension.

2. **Check `sender` only when it is not a known router.** Maintain a registry of trusted routers in the extension; when `sender` is a trusted router, extract the real user from `extensionData`; otherwise check `sender` directly.

Either approach ensures the allowlist gates the economically relevant actor regardless of which supported periphery path reaches the pool.

---

### Proof of Concept

```solidity
// Pool is deployed with SwapAllowlistExtension.
// Admin allowlists alice and the router so alice can trade via the router.
swapExt.setAllowedToSwap(pool, alice, true);
swapExt.setAllowedToSwap(pool, address(router), true);

// bob is NOT on the allowlist.
// bob calls the router directly — the extension sees msg.sender=router, passes.
vm.prank(bob);
router.exactInputSingle(ExactInputSingleParams({
    pool: pool,
    zeroForOne: true,
    amountIn: 1000,
    amountOutMinimum: 0,
    recipient: bob,
    priceLimitX64: 0,
    tokenIn: token0,
    deadline: block.timestamp,
    extensionData: ""
}));
// bob receives token1 output despite not being on the allowlist.
assertGt(token1.balanceOf(bob), 0);
```

The `SwapAllowlistExtension.beforeSwap` check evaluates `allowedSwapper[pool][router]` (true), so the guard passes for `bob` even though `allowedSwapper[pool][bob]` is false. [5](#0-4) [6](#0-5)

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

**File:** metric-core/contracts/ExtensionCalling.sol (L160-177)
```text
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
