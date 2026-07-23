Now I have enough to trace the full call path. Let me verify the `_validateOwner` function and the exact router flow to confirm the bypass.

### Title
`SwapAllowlistExtension.beforeSwap` gates on the router address instead of the actual end-user, allowing any unprivileged caller to bypass a curated pool's swap allowlist via `MetricOmmSimpleRouter` — (`File: metric-periphery/contracts/extensions/SwapAllowlistExtension.sol`)

---

### Summary

`SwapAllowlistExtension` is designed to restrict swaps on curated pools to a configured set of addresses. Its `beforeSwap` hook checks `allowedSwapper[pool][sender]`, where `sender` is the address that called `pool.swap()`. When swaps are routed through `MetricOmmSimpleRouter`, the pool's `msg.sender` is the router contract, not the end user. The extension therefore checks `allowedSwapper[pool][router]` instead of `allowedSwapper[pool][actual_user]`. Any pool admin who adds the router to the allowlist (the natural operational step to let allowlisted users use the router) simultaneously opens the allowlist to every unprivileged caller.

---

### Finding Description

**Call path:**

```
user → MetricOmmSimpleRouter.exactInputSingle(params)
         └─ IMetricOmmPoolActions(params.pool).swap(recipient, ...)
               pool.msg.sender = router
               └─ _beforeSwap(msg.sender=router, recipient, ...)
                     └─ SwapAllowlistExtension.beforeSwap(sender=router, ...)
                           checks: allowedSwapper[pool][router]   ← wrong actor
```

In `MetricOmmPool.swap`, the pool passes its own `msg.sender` as the `sender` argument to `_beforeSwap`:

```solidity
// MetricOmmPool.sol:230-240
_beforeSwap(
    msg.sender,   // ← router address when called via router
    recipient,
    ...
);
```

`ExtensionCalling._beforeSwap` forwards this verbatim to every configured extension:

```solidity
// ExtensionCalling.sol:160-176
_callExtensionsInOrder(
    BEFORE_SWAP_ORDER,
    abi.encodeCall(IMetricOmmExtensions.beforeSwap, (sender, ...))
);
```

`SwapAllowlistExtension.beforeSwap` then evaluates:

```solidity
// SwapAllowlistExtension.sol:37
if (!allowAllSwappers[msg.sender] && !allowedSwapper[msg.sender][sender]) {
    revert IMetricOmmPoolActions.NotAllowedToSwap();
}
```

Here `msg.sender` is the pool (correct namespace key) and `sender` is the router (wrong actor). The check resolves to `allowedSwapper[pool][router]`, not `allowedSwapper[pool][actual_user]`.

**Two concrete failure modes arise:**

1. **Allowlist bypass (critical path):** The pool admin adds the router to the allowlist so that allowlisted users can reach the pool through the supported periphery path. Once `allowedSwapper[pool][router] = true`, every unprivileged caller can bypass the allowlist by calling any of `exactInputSingle`, `exactInput`, `exactOutputSingle`, or `exactOutput` on the router. The extension sees `sender = router` and passes the check unconditionally.

2. **Broken core functionality (secondary path):** If the pool admin does not add the router, allowlisted users cannot use the router at all. The extension reverts with `NotAllowedToSwap` for every router-mediated swap, even for addresses that are individually allowlisted, because the check is on the router address, not the user.

The `DepositAllowlistExtension` does not share this flaw: it gates on `owner` (the position owner passed as a parameter), which is the economically relevant actor regardless of whether the call comes from the liquidity adder or directly.

---

### Impact Explanation

A curated pool deploying `SwapAllowlistExtension` to restrict trading (e.g., KYC-gated pools, pools restricted to specific market makers, or pools with regulatory constraints) loses its access control entirely once the router is allowlisted. Any unprivileged address can execute swaps against the pool's liquidity, extracting value from LP positions under conditions the pool admin explicitly intended to prevent. This is a direct loss of LP assets and a broken core pool invariant (the allowlist guard fails open on the primary supported swap path).

---

### Likelihood Explanation

`MetricOmmSimpleRouter` is the primary user-facing entry point for swaps. A pool admin who configures `SwapAllowlistExtension` and wants allowlisted users to use the router will inevitably add the router to the allowlist — this is the only apparent operational fix for the broken-functionality failure mode. The bypass is therefore a natural consequence of normal operational setup, reachable by any unprivileged caller with zero preconditions beyond the router being allowlisted.

---

### Recommendation

The `SwapAllowlistExtension` must gate on the actual end user, not the direct caller of `pool.swap()`. Since the pool only exposes `msg.sender` as `sender`, the fix requires one of:

1. **Router-forwarded identity via `extensionData`:** The router encodes the actual `msg.sender` into `extensionData`; the extension decodes and checks it when `sender` is a known trusted forwarder. This requires coordination between the router and extension.

2. **Trusted-forwarder slot in the pool interface:** Add an optional `originalSender` field to the swap call that the pool passes through to extensions, populated by the router with its own `msg.sender`.

3. **Gate on `recipient` with a strict policy:** Require that `recipient == msg.sender` at the router level and gate the allowlist on `recipient`. This is weaker but avoids the router-as-sender problem.

The simplest near-term mitigation is to document that `SwapAllowlistExtension` is incompatible with `MetricOmmSimpleRouter` for per-user gating, and to never allowlist the router address on pools where individual-user restrictions are enforced.

---

### Proof of Concept

```solidity
// Scenario: pool has SwapAllowlistExtension; only `allowedUser` is allowlisted.
// Pool admin adds the router to the allowlist so allowedUser can use it.
// Result: any attacker can bypass the allowlist via the router.

function testSwapAllowlistBypassViaRouter() public {
    // Setup: pool with SwapAllowlistExtension, allowedUser is allowlisted
    address allowedUser  = address(0xA11CE);
    address attacker     = address(0xBAD);

    vm.prank(poolAdmin);
    swapAllowlist.setAllowedToSwap(pool, allowedUser, true);

    // Pool admin adds router so allowedUser can use it
    vm.prank(poolAdmin);
    swapAllowlist.setAllowedToSwap(pool, address(router), true);

    // allowedUser can swap directly — expected
    vm.prank(allowedUser);
    pool.swap(allowedUser, true, 1e18, 0, "", "");

    // attacker bypasses allowlist via router — should revert but does NOT
    vm.prank(attacker);
    router.exactInputSingle(
        IMetricOmmSimpleRouter.ExactInputSingleParams({
            pool:            pool,
            recipient:       attacker,
            zeroForOne:      true,
            amountIn:        1e18,
            amountOutMinimum: 0,
            priceLimitX64:   0,
            deadline:        block.timestamp + 1,
            extensionData:   ""
        })
    );
    // Extension checked allowedSwapper[pool][router] = true → passed
    // attacker received output tokens despite not being allowlisted
}
```

The extension checks `allowedSwapper[pool][router]` (true) instead of `allowedSwapper[pool][attacker]` (false), so the guard passes and the attacker's swap executes. [1](#0-0) [2](#0-1) [3](#0-2) [4](#0-3) [5](#0-4)

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
