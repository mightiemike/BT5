### Title
`SwapAllowlistExtension` gates the router address instead of the originating user, allowing any unprivileged user to bypass the swap allowlist via `MetricOmmSimpleRouter` — (`metric-periphery/contracts/extensions/SwapAllowlistExtension.sol`)

---

### Summary

`SwapAllowlistExtension.beforeSwap` checks the `sender` argument, which is `msg.sender` of `pool.swap()`. When a user routes through `MetricOmmSimpleRouter`, the router contract becomes `msg.sender` of `pool.swap()`, so the allowlist checks the router's address rather than the originating user's address. If the pool admin allowlists the router (which is required for any allowlisted user to use the router), every unprivileged user can bypass the per-user swap restriction by routing through the public router.

---

### Finding Description

**Call path:**

```
User (not allowlisted)
  → MetricOmmSimpleRouter.exactInputSingle(...)
      → IMetricOmmPoolActions(pool).swap(recipient, zeroForOne, amount, limit, "", extensionData)
          // msg.sender = router
          → MetricOmmPool._beforeSwap(msg.sender=router, ...)
              → ExtensionCalling._callExtensionsInOrder(BEFORE_SWAP_ORDER, ...)
                  → SwapAllowlistExtension.beforeSwap(sender=router, ...)
                      // checks allowedSwapper[pool][router] — passes if router is allowlisted
```

In `MetricOmmPool.swap`, `msg.sender` (the router) is forwarded as `sender` to `_beforeSwap`: [1](#0-0) 

`ExtensionCalling._beforeSwap` encodes `sender` (the router) as the first argument to the extension: [2](#0-1) 

`SwapAllowlistExtension.beforeSwap` then checks `allowedSwapper[msg.sender][sender]`, where `msg.sender` is the pool and `sender` is the router: [3](#0-2) 

The router is a public, permissionless contract — any user can call it: [4](#0-3) 

**The structural trap:** The pool admin faces an impossible choice:
- **Do not allowlist the router** → allowlisted users cannot use the router at all (broken functionality).
- **Allowlist the router** → every user, including non-allowlisted ones, can bypass the restriction by routing through the public router (policy bypass).

By contrast, `DepositAllowlistExtension.beforeAddLiquidity` correctly gates by `owner` (the position owner, the economically relevant actor), not by `sender` (the payer/caller): [5](#0-4) 

The swap allowlist lacks this same design: it gates the wrong actor.

---

### Impact Explanation

A pool configured with `SwapAllowlistExtension` to restrict trading (e.g., KYC compliance, institutional-only pools, or curated market-making pools) can be fully bypassed by any unprivileged user routing through `MetricOmmSimpleRouter`. The bypassing user executes real swaps against pool liquidity, extracting value at oracle-derived prices that the pool admin intended to restrict. This is a direct loss of curation policy and, in pools where the allowlist is used to prevent adverse selection or regulatory exposure, a direct fund-impacting consequence for LPs.

---

### Likelihood Explanation

The bypass is reachable whenever the pool admin allowlists the router — a natural and expected operational step for any pool that wants allowlisted users to access the router. The `MetricOmmSimpleRouter` is a public, deployed periphery contract. No privileged setup by the attacker is required; the attacker only needs to call `exactInputSingle` or `exactInput` on the router pointing at the restricted pool.

---

### Recommendation

Gate the swap allowlist by the **originating user**, not the direct caller of `pool.swap()`. The pool passes both `sender` (direct caller) and `recipient` to the extension. For router-mediated swaps, the originating user is the router's `msg.sender`, which the router stores in transient storage as the payer. One correct approach:

1. Pass the originating user through `extensionData` (the router encodes it before calling the pool).
2. Alternatively, mirror the deposit allowlist design: gate by `recipient` (the economic beneficiary of the swap output) rather than `sender` (the direct caller), since `recipient` is set by the originating user and cannot be spoofed by the router.

The simplest fix consistent with the deposit allowlist pattern:

```solidity
// Gate by recipient (the economic beneficiary), not sender (the direct caller)
function beforeSwap(address, address recipient, bool, int128, uint128, uint256, uint128, uint128, bytes calldata)
    external view override returns (bytes4)
{
    if (!allowAllSwappers[msg.sender] && !allowedSwapper[msg.sender][recipient]) {
        revert IMetricOmmPoolActions.NotAllowedToSwap();
    }
    return IMetricOmmExtensions.beforeSwap.selector;
}
```

---

### Proof of Concept

```solidity
// Pool is configured with SwapAllowlistExtension.
// Admin allowlists the router so that allowlisted users can use it.
swapExtension.setAllowedToSwap(address(pool), address(router), true);

// Alice is NOT individually allowlisted.
// Alice calls the router directly — the router becomes msg.sender of pool.swap().
// The extension checks allowedSwapper[pool][router] → true → swap succeeds.
vm.prank(alice); // alice is not in allowedSwapper[pool]
router.exactInputSingle(ExactInputSingleParams({
    pool: address(pool),
    tokenIn: token0,
    recipient: alice,
    zeroForOne: true,
    amountIn: 1000e18,
    amountOutMinimum: 0,
    priceLimitX64: 0,
    deadline: block.timestamp,
    extensionData: ""
}));
// Alice successfully swaps on a pool she was supposed to be blocked from.
```

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
