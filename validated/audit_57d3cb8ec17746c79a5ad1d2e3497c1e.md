### Title
SwapAllowlistExtension gates the router address instead of the actual end user, allowing any user to bypass the per-user swap allowlist via MetricOmmSimpleRouter — (`metric-periphery/contracts/extensions/SwapAllowlistExtension.sol`)

---

### Summary

`SwapAllowlistExtension.beforeSwap()` checks `sender` — the immediate `msg.sender` of `pool.swap()` — against the per-pool allowlist. When a user routes through `MetricOmmSimpleRouter`, `sender` is the router's address, not the actual end user. This is the direct analog of H-02: the allowlist check does not account for the source context (direct call vs. router-mediated call), so the identity it gates is wrong.

---

### Finding Description

`MetricOmmPool.swap()` passes `msg.sender` as the `sender` argument to every configured extension:

```solidity
// MetricOmmPool.sol:230-240
_beforeSwap(
  msg.sender,   // ← whoever called pool.swap()
  recipient,
  ...
  extensionData
);
```

`SwapAllowlistExtension.beforeSwap()` then checks that `sender` against the per-pool allowlist, where `msg.sender` inside the extension is the pool:

```solidity
// SwapAllowlistExtension.sol:37-39
if (!allowAllSwappers[msg.sender] && !allowedSwapper[msg.sender][sender]) {
  revert IMetricOmmPoolActions.NotAllowedToSwap();
}
```

When a user calls `MetricOmmSimpleRouter.exactInputSingle()`, the router calls `pool.swap()` with itself as `msg.sender`:

```solidity
// MetricOmmSimpleRouter.sol:72-80
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

So the extension receives `sender = router`, not `sender = user`. The allowlist lookup becomes `allowedSwapper[pool][router]`, not `allowedSwapper[pool][user]`.

This creates two mutually exclusive broken states:

1. **Router not allowlisted**: Allowlisted users cannot swap through the router at all — `NotAllowedToSwap` reverts for every router-mediated call, breaking core swap functionality for the intended user set.

2. **Router allowlisted** (the only way to enable router-mediated swaps): `allowedSwapper[pool][router] = true` passes the check for every user who calls through the router, regardless of whether that user is individually allowlisted. Any non-allowlisted address can bypass the guard by calling `router.exactInputSingle()` instead of `pool.swap()` directly.

The `extensionData` field is fully user-controlled and cannot be used to pass a trusted caller identity. There is no mechanism in the current hook interface for the extension to recover the original end user's address when the call is router-mediated.

---

### Impact Explanation

When the router is allowlisted (the only configuration that lets allowlisted users trade via the router), the swap allowlist is completely bypassed for all router-mediated swaps. Any address — including addresses the pool admin explicitly excluded — can execute swaps against the pool by routing through `MetricOmmSimpleRouter`. This breaks the core access-control invariant of the extension and directly enables unauthorized swaps, which can drain LP value through unfavorable oracle-priced trades by actors the pool admin intended to block.

---

### Likelihood Explanation

`MetricOmmSimpleRouter` is the primary user-facing entry point for swaps. Any pool that deploys `SwapAllowlistExtension` to restrict trading to a curated set of addresses (e.g., KYC'd counterparties, whitelisted market makers) will face this issue the moment the pool admin allowlists the router to support normal UX. The router is a public, permissionless contract — no special privilege or setup is required for an attacker to exploit this path.

---

### Recommendation

The allowlist must gate the actual end user, not the immediate caller of `pool.swap()`. Two viable approaches:

1. **Trusted forwarding in extensionData**: Define a standard encoding where the router prepends the original `msg.sender` into `extensionData` before calling `pool.swap()`. The extension then reads the user address from `extensionData` only when `sender` is a known, factory-registered router. This requires the extension to maintain a registry of trusted routers.

2. **Separate router-aware allowlist**: Add a `mapping(address router => bool)` to the extension. When `sender` is a trusted router, decode the actual user from `extensionData`; otherwise use `sender` directly. This preserves backward compatibility for direct pool calls.

Either approach must ensure the user address in `extensionData` cannot be spoofed by a non-router caller.

---

### Proof of Concept

**Setup**: Pool is deployed with `SwapAllowlistExtension` configured as a `beforeSwap` hook. Pool admin allowlists `alice` as a permitted swapper and allowlists the router so that `alice` can trade via the router.

**Attack**:
1. `bob` (not allowlisted) calls `router.exactInputSingle({pool: pool, ..., recipient: bob})`.
2. Router calls `pool.swap(bob, ...)` with `msg.sender = router`.
3. Pool calls `extension.beforeSwap(sender=router, ...)`.
4. Extension checks `allowedSwapper[pool][router]` → `true` (set by admin to enable alice's router trades).
5. Swap executes. `bob` receives output tokens despite never being allowlisted.

**Direct call for comparison**:
1. `bob` calls `pool.swap(bob, ...)` directly.
2. Pool calls `extension.beforeSwap(sender=bob, ...)`.
3. Extension checks `allowedSwapper[pool][bob]` → `false`.
4. Reverts with `NotAllowedToSwap`.

The same non-allowlisted address is blocked on the direct path but succeeds on the router path, demonstrating the bypass. [1](#0-0) [2](#0-1) [3](#0-2)

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
