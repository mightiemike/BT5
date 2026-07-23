### Title
`SwapAllowlistExtension` Gates the Router Address Instead of the Actual Swapper, Allowing Full Allowlist Bypass via `MetricOmmSimpleRouter` — (`metric-periphery/contracts/extensions/SwapAllowlistExtension.sol`)

---

### Summary

`SwapAllowlistExtension.beforeSwap` receives `sender` from the pool, which is the pool's own `msg.sender` — the router contract — not the end user. When a pool admin allowlists the router to permit router-mediated swaps, every public user of the router automatically passes the gate, defeating the allowlist entirely.

---

### Finding Description

`MetricOmmPool.swap` passes `msg.sender` as the first argument to `_beforeSwap`:

```solidity
// metric-core/contracts/MetricOmmPool.sol
_beforeSwap(
    msg.sender,   // ← the router when called via MetricOmmSimpleRouter
    recipient,
    ...
);
```

`ExtensionCalling._beforeSwap` forwards that value unchanged as `sender` to every configured extension:

```solidity
// metric-core/contracts/ExtensionCalling.sol
abi.encodeCall(
    IMetricOmmExtensions.beforeSwap,
    (sender, recipient, zeroForOne, amountSpecified, priceLimitX64,
     packedSlot0Initial, bidPriceX64, askPriceX64, extensionData)
)
```

`SwapAllowlistExtension.beforeSwap` then checks that `sender` value against the per-pool allowlist:

```solidity
// metric-periphery/contracts/extensions/SwapAllowlistExtension.sol
function beforeSwap(address sender, address, bool, int128, uint128, uint256, uint128, uint128, bytes calldata)
    external view override returns (bytes4)
{
    if (!allowAllSwappers[msg.sender] && !allowedSwapper[msg.sender][sender]) {
        revert IMetricOmmPoolActions.NotAllowedToSwap();
    }
    return IMetricOmmExtensions.beforeSwap.selector;
}
```

When a user calls `MetricOmmSimpleRouter.exactInputSingle` (or any other router entry point), the router calls `pool.swap(...)`. The pool's `msg.sender` is the router, so `sender` arriving at the extension is the router's address, not the user's address. The check becomes `allowedSwapper[pool][router]`, not `allowedSwapper[pool][user]`.

---

### Impact Explanation

A pool admin who wants to restrict swaps to a specific set of users (e.g., KYC'd counterparties, whitelisted market makers) deploys the pool with `SwapAllowlistExtension`. To let those users interact through the standard `MetricOmmSimpleRouter`, the admin must also call `setAllowedToSwap(pool, router, true)`. The moment the router is allowlisted, the gate is open to every public caller of the router — any address can call `exactInputSingle` / `exactInput` / `exactOutput` and the extension will pass them because it sees the router, not the user. Unauthorized users can drain LP positions at oracle-quoted prices, causing direct loss of LP principal. The pool admin has no way to simultaneously allow router-mediated swaps for legitimate users and block unauthorized users, because the extension has no visibility into the real initiator.

---

### Likelihood Explanation

The router is the primary user-facing entry point documented and expected by the protocol. Any pool admin who configures a swap allowlist and also wants their allowlisted users to use the router will inevitably allowlist the router, triggering the bypass. The attacker needs no special privilege: a single public call to `MetricOmmSimpleRouter.exactInputSingle` suffices. The condition is reachable on every allowlisted pool that also permits router access.

---

### Recommendation

The extension must check the economically relevant actor — the end user — not the intermediary. Two complementary fixes:

1. **Pass the original initiator through the hook.** The pool could forward an additional `initiator` field (the address that originally called the router, recoverable from transient storage the router already writes) alongside `sender`. The extension would then gate on `initiator`.

2. **Check `sender` only when `sender` is not a known router; otherwise require the router to forward the real user in `extensionData`.** The router would encode `msg.sender` into `extensionData`, and the extension would decode and check it when `sender` is the router address.

The simplest safe fix is option 1: store the real user in transient storage at the router entry point and expose it to extensions via a dedicated slot that the pool reads and forwards.

---

### Proof of Concept

```
Setup:
  pool configured with SwapAllowlistExtension
  allowedSwapper[pool][alice] = true          // alice is the only allowed user
  allowedSwapper[pool][router] = true         // admin adds router so alice can use it

Attack:
  bob (not allowlisted) calls:
    router.exactInputSingle({pool: pool, tokenIn: token0, ...})

  Execution path:
    router.exactInputSingle()
      → pool.swap(recipient, zeroForOne, amount, limit, "", extensionData)
          msg.sender = router
        → _beforeSwap(sender=router, ...)
          → SwapAllowlistExtension.beforeSwap(sender=router, ...)
              allowedSwapper[pool][router] == true  ✓  (passes!)
        → swap executes, bob receives token1 output

Result: bob, who is not on the allowlist, successfully swaps against the restricted pool.
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
