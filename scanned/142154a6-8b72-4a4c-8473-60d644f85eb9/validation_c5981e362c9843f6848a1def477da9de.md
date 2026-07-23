### Title
SwapAllowlistExtension Checks Router Address Instead of Actual User, Enabling Full Allowlist Bypass via MetricOmmSimpleRouter — (`metric-periphery/contracts/extensions/SwapAllowlistExtension.sol`)

---

### Summary

`SwapAllowlistExtension.beforeSwap` gates swaps by checking `allowedSwapper[msg.sender][sender]`, where `sender` is the `msg.sender` of the pool's `swap()` call. When a user routes through `MetricOmmSimpleRouter`, the pool sees the **router** as `msg.sender`, so the extension checks whether the **router** is allowlisted — not the actual user. A pool admin who allowlists the router (the only way to let allowlisted users trade via the router) simultaneously opens the gate to every non-allowlisted user on the internet.

---

### Finding Description

**Call chain for a router-mediated swap:**

```
User (non-allowlisted) 
  → MetricOmmSimpleRouter.exactInputSingle()
      → IMetricOmmPoolActions(pool).swap(recipient, ..., extensionData)
          // pool sees msg.sender = router
          → _beforeSwap(msg.sender=router, ...)
              → SwapAllowlistExtension.beforeSwap(sender=router, ...)
                  checks: allowedSwapper[pool][router]   ← router, NOT the user
```

In `MetricOmmPool.swap`, `msg.sender` (the router) is forwarded as `sender` to the extension: [1](#0-0) 

`ExtensionCalling._beforeSwap` passes that `sender` value unchanged to every configured extension: [2](#0-1) 

`SwapAllowlistExtension.beforeSwap` then evaluates `allowedSwapper[msg.sender][sender]`, where `msg.sender` is the pool and `sender` is whoever called `pool.swap()` — the router, not the end user: [3](#0-2) 

`MetricOmmSimpleRouter.exactInputSingle` calls `pool.swap()` directly with no mechanism to forward the original `msg.sender`: [4](#0-3) 

This creates an impossible dilemma for the pool admin:

| Router allowlist state | Effect on allowlisted users | Effect on non-allowlisted users |
|---|---|---|
| Router **not** allowlisted | Cannot use router (blocked) | Correctly blocked |
| Router **allowlisted** | Can use router | **Bypass: anyone can swap** |

There is no configuration that simultaneously allows allowlisted users to use the router and blocks non-allowlisted users.

---

### Impact Explanation

A pool deployer using `SwapAllowlistExtension` to create a curated (e.g., KYC-gated, institutional, or permissioned) pool cannot enforce the allowlist for router-mediated swaps. Any non-allowlisted user can call `MetricOmmSimpleRouter.exactInputSingle` / `exactInput` / `exactOutputSingle` / `exactOutput` against the pool and trade freely, bypassing the intended access control. This constitutes a direct policy bypass on curated pools with fund-impacting consequences (unauthorized users drain LP value at oracle-derived prices).

---

### Likelihood Explanation

The `MetricOmmSimpleRouter` is the primary user-facing swap entrypoint. A pool admin who configures a swap allowlist almost certainly intends for allowlisted users to be able to use the router. Allowlisting the router is the natural operational step, and it is the step that opens the bypass. The attacker requires no special privileges, no malicious setup, and no unusual token behavior — only a call to the public router.

---

### Recommendation

The extension must gate the **original end-user**, not the intermediary. Two sound approaches:

1. **Pass original caller through the router.** Have `MetricOmmSimpleRouter` store `msg.sender` in transient storage (as it already does for the payer in `_setNextCallbackContext`) and expose it via a standard interface that extensions can query during the hook. The pool would forward this value as `sender` instead of its own `msg.sender`.

2. **Check `sender` against the allowlist at the pool level, not the extension level.** The pool already knows `msg.sender`; a dedicated allowlist that the pool enforces natively (rather than via an extension hook that only sees the intermediary) would be immune to this indirection.

Until fixed, pool admins should be warned that `SwapAllowlistExtension` provides **no protection** for swaps routed through `MetricOmmSimpleRouter`.

---

### Proof of Concept

```solidity
// Setup: pool with SwapAllowlistExtension; only `alice` is allowlisted.
// Pool admin allowlists the router so alice can trade via it.
swapExtension.setAllowedToSwap(address(pool), address(router), true);

// Attacker (not allowlisted) bypasses the guard via the router:
vm.prank(attacker);
router.exactInputSingle(
    IMetricOmmSimpleRouter.ExactInputSingleParams({
        pool:            address(pool),
        tokenIn:         address(token0),
        tokenOut:        address(token1),
        zeroForOne:      true,
        amountIn:        1_000e18,
        amountOutMinimum: 0,
        recipient:       attacker,
        deadline:        block.timestamp + 1,
        priceLimitX64:   0,
        extensionData:   ""
    })
);
// Succeeds: extension checked allowedSwapper[pool][router] == true,
// never checked allowedSwapper[pool][attacker].
```

The extension's `beforeSwap` receives `sender = address(router)`, which is allowlisted, so the check passes for the attacker. [5](#0-4) [6](#0-5)

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
