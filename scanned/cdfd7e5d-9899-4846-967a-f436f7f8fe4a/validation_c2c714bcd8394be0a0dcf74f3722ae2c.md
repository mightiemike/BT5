### Title
`SwapAllowlistExtension` Gates the Router Address Instead of the Actual User, Allowing Any Caller to Bypass the Swap Allowlist via `MetricOmmSimpleRouter` — (`File: metric-periphery/contracts/extensions/SwapAllowlistExtension.sol`)

---

### Summary

`SwapAllowlistExtension.beforeSwap` checks the `sender` argument passed by the pool, which is `msg.sender` of `pool.swap()`. When a user routes through `MetricOmmSimpleRouter`, that `msg.sender` is the **router contract**, not the actual user. If the pool admin allowlists the router (the natural step to enable router-mediated swaps), every unprivileged user can bypass the curated allowlist by routing through the router.

---

### Finding Description

**Call chain:**

```
user → MetricOmmSimpleRouter.exactInputSingle()
         → pool.swap(recipient, zeroForOne, amount, ..., extensionData)
              msg.sender = router
              → _beforeSwap(msg.sender=router, recipient, ...)
                   → SwapAllowlistExtension.beforeSwap(sender=router, ...)
                        checks: allowedSwapper[pool][router]
```

In `MetricOmmPool.swap`, the pool passes its own `msg.sender` as the `sender` argument to `_beforeSwap`:

```solidity
// MetricOmmPool.sol line 230-240
_beforeSwap(
    msg.sender,   // ← router address when called via router
    recipient,
    ...
);
```

`ExtensionCalling._beforeSwap` forwards this verbatim to the extension:

```solidity
// ExtensionCalling.sol line 160-176
abi.encodeCall(
    IMetricOmmExtensions.beforeSwap,
    (sender, recipient, ...)  // sender = router
)
```

`SwapAllowlistExtension.beforeSwap` then checks:

```solidity
// SwapAllowlistExtension.sol line 37-39
if (!allowAllSwappers[msg.sender] && !allowedSwapper[msg.sender][sender]) {
    revert IMetricOmmPoolActions.NotAllowedToSwap();
}
// msg.sender = pool (correct key), sender = router (wrong actor)
```

The allowlist lookup is `allowedSwapper[pool][router]`, not `allowedSwapper[pool][actual_user]`.

**The bypass:** A pool admin who wants to allow router-mediated swaps for allowlisted users will call `setAllowedToSwap(pool, router, true)`. Once the router is allowlisted, `allowedSwapper[pool][router]` is `true` for every call that arrives through the router — regardless of who the actual user is. Any address, including one that was never individually allowlisted, can now swap on the curated pool by calling `router.exactInputSingle()` or `router.exactInput()`.

**Contrast with `DepositAllowlistExtension`:** The deposit extension correctly ignores the first (`sender`) argument and gates on `owner` — the LP position owner — which is preserved end-to-end through the liquidity adder. The swap extension has no equivalent "real actor" argument to fall back on; it only receives `sender`, which is the router.

---

### Impact Explanation

A pool configured with `SwapAllowlistExtension` to restrict trading to a curated set of counterparties (e.g., KYC'd market makers, whitelisted institutions, or protocol-controlled addresses) loses that restriction entirely for any user who routes through `MetricOmmSimpleRouter`. The pool's LP assets are exposed to unrestricted swaps, which can drain liquidity at oracle-derived prices from any caller. This is a direct loss of LP principal and a complete failure of the pool's core access-control invariant.

---

### Likelihood Explanation

The trigger requires only that the pool admin allowlists the router address — a routine operational step for any pool that intends to support the standard periphery. No privileged escalation, no malicious setup, and no non-standard tokens are required. Any unprivileged user who observes the allowlist state can immediately exploit it by calling the public router.

---

### Recommendation

Pass the **original user** through the swap path so the extension can gate on the economically relevant actor. Two options:

1. **Add a `payer`/`originator` field to the swap call or extension payload** so the router can forward `msg.sender` (the real user) alongside the pool's `msg.sender` (the router). The extension would then check the originator.

2. **Check `recipient` instead of `sender`** if the pool's design guarantees that the recipient is always the real user. This is fragile for multi-hop paths where intermediate recipients are the router itself.

The cleanest fix is option 1: the router stores the real user in transient storage (it already does this for the payer in `_setNextCallbackContext`) and encodes it into `extensionData` so the extension can decode and check it.

---

### Proof of Concept

```solidity
// Setup: pool with SwapAllowlistExtension, router allowlisted
swapExtension.setAllowedToSwap(address(pool), address(router), true);
// attacker is NOT individually allowlisted

// Attacker bypasses allowlist via router
vm.prank(attacker);
router.exactInputSingle(
    IMetricOmmSimpleRouter.ExactInputSingleParams({
        pool:            address(pool),
        tokenIn:         address(token0),
        tokenOut:        address(token1),
        zeroForOne:      true,
        amountIn:        1_000,
        amountOutMinimum: 0,
        recipient:       attacker,
        deadline:        block.timestamp + 1,
        priceLimitX64:   0,
        extensionData:   ""
    })
);
// ↑ succeeds: pool.swap() is called with msg.sender=router,
//   extension checks allowedSwapper[pool][router] == true → passes
//   attacker drains pool liquidity despite never being allowlisted
```

**Relevant code locations:** [1](#0-0) [2](#0-1) [3](#0-2) [4](#0-3) [5](#0-4)

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
