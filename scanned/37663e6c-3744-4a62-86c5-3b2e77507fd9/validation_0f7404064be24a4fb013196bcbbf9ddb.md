### Title
SwapAllowlistExtension checks the router's address instead of the actual user, allowing any unprivileged user to bypass the swap allowlist via MetricOmmSimpleRouter — (`metric-periphery/contracts/extensions/SwapAllowlistExtension.sol`)

---

### Summary

`SwapAllowlistExtension.beforeSwap` gates swaps by checking `allowedSwapper[pool][sender]`, where `sender` is `msg.sender` of `pool.swap()`. When a swap is routed through `MetricOmmSimpleRouter`, `msg.sender` of `pool.swap()` is the **router contract**, not the actual user. If the pool admin allowlists the router (the only way to support router-mediated swaps for any user), every unprivileged address can bypass the allowlist by calling through the router.

---

### Finding Description

**Call path:**

```
user → MetricOmmSimpleRouter.exactInputSingle()
     → IMetricOmmPoolActions(pool).swap(recipient, ...)   // msg.sender = router
     → MetricOmmPool.swap()
         → _beforeSwap(msg.sender, ...)                   // sender = router address
         → ExtensionCalling._callExtensionsInOrder()
         → SwapAllowlistExtension.beforeSwap(sender=router, ...)
             → allowedSwapper[pool][router]               // checks router, not user
```

In `MetricOmmPool.swap`, the pool unconditionally passes `msg.sender` as the `sender` argument to `_beforeSwap`:

```solidity
// MetricOmmPool.sol line 230-240
_beforeSwap(
  msg.sender,   // <-- always the direct caller; router when routed
  recipient,
  ...
);
```

`ExtensionCalling._beforeSwap` forwards this verbatim to every configured extension:

```solidity
// ExtensionCalling.sol line 162-165
abi.encodeCall(
  IMetricOmmExtensions.beforeSwap,
  (sender, recipient, ...)
)
```

`SwapAllowlistExtension.beforeSwap` then checks the received `sender` against the per-pool allowlist:

```solidity
// SwapAllowlistExtension.sol line 37-39
if (!allowAllSwappers[msg.sender] && !allowedSwapper[msg.sender][sender]) {
  revert IMetricOmmPoolActions.NotAllowedToSwap();
}
```

`msg.sender` here is the pool (correct key namespace). `sender` is the router address when the swap enters via `MetricOmmSimpleRouter.exactInputSingle` / `exactInput` / `exactOutputSingle` / `exactOutput`, because the router calls `pool.swap()` directly without forwarding the originating user:

```solidity
// MetricOmmSimpleRouter.sol line 72-80
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

The router stores the originating `msg.sender` only in transient storage for the payment callback (`_setNextCallbackContext`), not in any argument visible to the pool or its extensions.

**The dilemma this creates for pool admins:**

| Admin choice | Effect |
|---|---|
| Do **not** allowlist the router | Allowlisted users cannot use the router at all |
| Allowlist the router | **Every** user can bypass the allowlist via the router |

There is no configuration that simultaneously allows router-mediated swaps for allowlisted users and blocks non-allowlisted users.

---

### Impact Explanation

A curated pool deploying `SwapAllowlistExtension` to restrict trading to specific counterparties (e.g., KYC'd addresses, protocol-owned bots, or whitelisted market makers) is fully bypassed the moment the router is allowlisted. Any unprivileged address can call `MetricOmmSimpleRouter.exactInputSingle` and execute swaps against the pool's LP positions. LPs who deposited under the assumption that only trusted counterparties would trade against them are exposed to arbitrary toxic flow, sandwich attacks, or oracle-price extraction from any address. This is a direct loss of LP principal through bad-price execution that the configured allowlist was supposed to prevent.

---

### Likelihood Explanation

The trigger requires only that the pool admin allowlists the router — a natural and expected operational step for any curated pool that wants to support standard periphery tooling. No privileged escalation, no malicious setup, and no special token behavior is needed. Any user who can call `MetricOmmSimpleRouter` can exploit this immediately after the router is allowlisted.

---

### Recommendation

The pool's `swap` function signature does not expose an explicit `sender` parameter; it always uses `msg.sender`. Two remediation paths exist:

1. **Router-side**: Add an explicit `sender` parameter to the pool's `swap` interface (or a separate trusted-forwarder pattern) so the router can pass the originating user's address. The extension then checks that address.

2. **Extension-side**: `SwapAllowlistExtension` should not be used with pools that are expected to receive swaps through the router unless the allowlist is intentionally set to `allowAllSwappers`. Document this constraint clearly, or add a router-aware check that reads the originating user from a trusted forwarder context.

---

### Proof of Concept

```solidity
// Setup: pool with SwapAllowlistExtension; only `alice` is allowlisted.
// Pool admin also allowlists the router so alice can use it.
swapExtension.setAllowedToSwap(address(pool), alice, true);
swapExtension.setAllowedToSwap(address(pool), address(router), true); // needed for alice to use router

// Attack: bob (not allowlisted) calls the router directly.
vm.startPrank(bob);
token0.approve(address(router), type(uint256).max);
router.exactInputSingle(
    IMetricOmmSimpleRouter.ExactInputSingleParams({
        pool: address(pool),
        tokenIn: address(token0),
        recipient: bob,
        deadline: block.timestamp,
        zeroForOne: true,
        amountIn: 1000e18,
        amountOutMinimum: 0,
        priceLimitX64: 0,
        extensionData: ""
    })
);
// Succeeds: extension sees sender=router (allowlisted), not bob (not allowlisted).
// Bob has bypassed the swap allowlist entirely.
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
