### Title
`SwapAllowlistExtension` Gates the Router Address Instead of the Actual Swapper, Enabling Full Allowlist Bypass via `MetricOmmSimpleRouter` - (`metric-periphery/contracts/extensions/SwapAllowlistExtension.sol`)

---

### Summary

`SwapAllowlistExtension.beforeSwap` checks the `sender` argument forwarded by the pool, which equals `msg.sender` of the `pool.swap(...)` call — the router contract — not the end user. When a pool admin allowlists the `MetricOmmSimpleRouter` to let curated users reach the pool through the standard periphery, every unprivileged user can bypass the allowlist by routing through the same contract.

---

### Finding Description

`SwapAllowlistExtension.beforeSwap` enforces the allowlist as follows:

```solidity
// metric-periphery/contracts/extensions/SwapAllowlistExtension.sol L31-41
function beforeSwap(address sender, address, bool, int128, uint128, uint256, uint128, uint128, bytes calldata)
    external view override returns (bytes4)
{
    if (!allowAllSwappers[msg.sender] && !allowedSwapper[msg.sender][sender]) {
        revert IMetricOmmPoolActions.NotAllowedToSwap();
    }
    return IMetricOmmExtensions.beforeSwap.selector;
}
```

`msg.sender` here is the pool (correct). `sender` is the first argument the pool passes to `_beforeSwap`, which is the pool's own `msg.sender` — the direct caller of `pool.swap(...)`.

`MetricOmmSimpleRouter.exactInputSingle` calls the pool as:

```solidity
// metric-periphery/contracts/MetricOmmSimpleRouter.sol L72-80
IMetricOmmPoolActions(params.pool).swap(
    params.recipient,
    params.zeroForOne,
    MetricOmmSwapInputs.asAmountSpecifiedIn(params.amountIn),
    priceLimitX64,
    "",
    params.extensionData
);
```

The pool's `msg.sender` is the router. The pool therefore calls `_beforeSwap(router, recipient, ...)`, and the extension receives `sender = router`. The check `allowedSwapper[pool][router]` is evaluated — not `allowedSwapper[pool][actual_user]`.

The same pattern applies to `exactInput`, `exactOutputSingle`, and `exactOutput`.

**Two broken invariants arise:**

1. **Allowlist bypass (High impact):** A pool admin who wants curated users to reach the pool through the standard periphery must allowlist the router address. Once the router is allowlisted, `allowedSwapper[pool][router] == true` for every swap that arrives through it, so any non-allowlisted user can call `exactInputSingle` and pass the guard.

2. **Broken core functionality (Medium impact):** If the admin does *not* allowlist the router, every allowlisted user is blocked from using the supported periphery path and must call the pool directly — defeating the purpose of the periphery layer.

---

### Impact Explanation

A curated pool's swap allowlist is completely ineffective for router-mediated swaps. Any user can call `MetricOmmSimpleRouter.exactInputSingle` (or any other router entry point) on an allowlisted pool and execute a swap that the pool admin intended to restrict. This constitutes a direct admin-boundary break and a policy bypass on curated pools, with fund-impacting consequences (e.g., a pool designed for KYC-only participants becomes open to all).

---

### Likelihood Explanation

The `MetricOmmSimpleRouter` is the primary user-facing swap interface. Pool admins who deploy a curated pool with a swap allowlist will naturally also allowlist the router to give their approved users a standard UX. The bypass is then reachable by any unprivileged user with a single public call. No special privileges, flash loans, or unusual token behavior are required.

---

### Recommendation

The extension must check the economically relevant actor — the end user — not the intermediary. Two complementary fixes:

1. **Pass the original initiator through the pool.** The pool should forward the original `msg.sender` (or an explicit `initiator` parameter) separately from the routing intermediary, and the extension should check that value.

2. **Short-term mitigation:** Document that `SwapAllowlistExtension` is incompatible with router-mediated swaps and must only be used with direct pool calls, or add a router-aware wrapper that checks `tx.origin` (with the known caveats) or a signed permit.

---

### Proof of Concept

```
Setup:
  pool = MetricOmmPool with SwapAllowlistExtension in beforeSwap slot
  admin allowlists alice:   allowedSwapper[pool][alice] = true
  admin allowlists router:  allowedSwapper[pool][router] = true
    (necessary so alice can use the standard periphery)

Attack (bob, not allowlisted):
  bob calls MetricOmmSimpleRouter.exactInputSingle({
      pool: pool,
      recipient: bob,
      ...
  })

  Router calls pool.swap(bob, zeroForOne, amount, limit, "", extensionData)
    pool.msg.sender = router
    pool calls _beforeSwap(router, bob, ...)
    extension checks allowedSwapper[pool][router] → true
    guard passes → bob's swap executes

Result:
  bob receives output tokens from a pool that was supposed to be restricted to alice only.
  The allowlist is fully bypassed through the supported public periphery path.
``` [1](#0-0) [2](#0-1) [3](#0-2)

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
