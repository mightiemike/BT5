### Title
`SwapAllowlistExtension.beforeSwap` checks the router address instead of the actual swapper, enabling allowlist bypass via `MetricOmmSimpleRouter` — (File: metric-periphery/contracts/extensions/SwapAllowlistExtension.sol)

---

### Summary

`SwapAllowlistExtension` gates swaps by checking the `sender` argument forwarded from the pool, which equals `msg.sender` of `pool.swap()`. When a user routes through `MetricOmmSimpleRouter`, `sender` is the router contract address, not the actual user. If the pool admin allowlists the router—a natural action to enable router-mediated swaps for their users—every user, including those not individually allowlisted, can bypass the curated-pool swap gate by routing through the router.

---

### Finding Description

`SwapAllowlistExtension.beforeSwap` performs:

```solidity
if (!allowAllSwappers[msg.sender] && !allowedSwapper[msg.sender][sender]) {
    revert IMetricOmmPoolActions.NotAllowedToSwap();
}
```

`msg.sender` here is the pool (the extension caller). `sender` is the value the pool received as `msg.sender` of its own `swap()` call. [1](#0-0) 

In `MetricOmmPool.swap()`, the pool passes `msg.sender` directly as the `sender` argument to `_beforeSwap`: [2](#0-1) 

When `MetricOmmSimpleRouter.exactInputSingle()` (or `exactInput`, `exactOutput`, `exactOutputSingle`) calls `pool.swap()`, the pool's `msg.sender` is the router contract: [3](#0-2) 

The actual user who initiated the router call is stored only in transient callback storage (`_setNextCallbackContext`) and is never forwarded to the extension. The extension therefore evaluates `allowedSwapper[pool][router]`, not `allowedSwapper[pool][actual_user]`.

**Exploit path:**

1. Pool admin deploys a curated pool with `SwapAllowlistExtension` and individual user allowlist entries.
2. Pool admin calls `extension.setAllowedToSwap(pool, router, true)` to let their allowlisted users reach the pool through the router.
3. A non-allowlisted attacker calls `MetricOmmSimpleRouter.exactInputSingle(pool, ...)`.
4. Router calls `pool.swap()` → pool passes `sender = router` to `_beforeSwap` → extension checks `allowedSwapper[pool][router]` → `true` → swap executes.
5. The individual allowlist is completely bypassed.

The `DepositAllowlistExtension` does **not** share this flaw: it checks `owner` (the position owner, which is the economically relevant actor), not `sender` (the immediate caller): [4](#0-3) 

The swap extension checks the wrong actor, breaking the invariant the contest scope explicitly identifies: *"swap allowlist checks must cover the exact actor/action intended and cannot be bypassed through router."* [5](#0-4) 

---

### Impact Explanation

A curated pool with `SwapAllowlistExtension` is designed to restrict trading to a specific set of authorized addresses. Once the pool admin allowlists the router (to give their authorized users access to multi-hop routing), the allowlist is rendered ineffective: any unprivileged user can swap on the curated pool by routing through `MetricOmmSimpleRouter`. This breaks the core access-control invariant of the extension and constitutes an admin-boundary break via an unprivileged path.

---

### Likelihood Explanation

**Medium.** The bypass requires the pool admin to have allowlisted the router. This is a natural operational step: a pool admin who wants their authorized users to benefit from multi-hop routing or the router's slippage protection will add the router to the allowlist. The admin is unlikely to realize that doing so opens the gate to all users, because the extension's `sender`-based check is inconsistent with the `owner`-based check used by the deposit allowlist in the same codebase.

---

### Recommendation

The extension must gate the economically relevant actor—the user who initiated the swap—not the immediate caller of `pool.swap()`. Two options:

1. **Pass the real user via `extensionData`:** Have the router encode `msg.sender` (the actual user) into `extensionData` and have the extension decode and check it. This requires a coordinated change to the router and extension.
2. **Document and enforce direct-pool-only access:** Explicitly document that pools using `SwapAllowlistExtension` must not allowlist the router, and that allowlisted users must call `pool.swap()` directly. Add a comment or NatSpec warning to the extension.

Option 1 is the only complete fix; option 2 is a mitigation that relies on operator discipline.

---

### Proof of Concept

```solidity
// Setup
SwapAllowlistExtension ext = new SwapAllowlistExtension(factory);
// Pool admin allowlists the router (intending to let authorized users use it)
ext.setAllowedToSwap(pool, address(router), true);
// alice is NOT individually allowlisted
// assert(!ext.isAllowedToSwap(pool, alice));

// Attack: alice routes through the router
vm.prank(alice);
router.exactInputSingle(ExactInputSingleParams({
    pool: pool,
    tokenIn: token0,
    recipient: alice,
    amountIn: 1000,
    amountOutMinimum: 0,
    zeroForOne: true,
    priceLimitX64: type(uint128).max,
    deadline: block.timestamp,
    extensionData: ""
}));
// Swap succeeds — alice bypassed the allowlist via the router
``` [1](#0-0) [6](#0-5)

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
