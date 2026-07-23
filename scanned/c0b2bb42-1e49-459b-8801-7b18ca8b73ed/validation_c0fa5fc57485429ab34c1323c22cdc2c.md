### Title
`SwapAllowlistExtension.beforeSwap` gates the router address instead of the end user, allowing any caller to bypass the per-user swap allowlist via `MetricOmmSimpleRouter` — (`metric-periphery/contracts/extensions/SwapAllowlistExtension.sol`)

---

### Summary

`SwapAllowlistExtension` is designed to restrict which addresses may swap against a pool. Its `beforeSwap` hook checks the `sender` argument passed by the pool, which is `msg.sender` from the pool's perspective — the **immediate caller of `pool.swap()`**. When a user routes through `MetricOmmSimpleRouter`, the router is the immediate caller, so the allowlist check evaluates the **router's address** rather than the **end user's address**. If the pool admin allowlists the router (a natural operational step to support router-mediated swaps), every user — including those individually blocked — can bypass the restriction by routing through the router.

---

### Finding Description

**Step 1 — Pool passes `msg.sender` as `sender` to the hook.**

`MetricOmmPool.swap()` calls `_beforeSwap` with `msg.sender` as the first argument: [1](#0-0) 

`ExtensionCalling._beforeSwap` forwards that value unchanged as the `sender` parameter to every configured extension: [2](#0-1) 

**Step 2 — The allowlist checks `sender`, which is the router when routing is used.**

`SwapAllowlistExtension.beforeSwap` evaluates:

```solidity
if (!allowAllSwappers[msg.sender] && !allowedSwapper[msg.sender][sender]) {
    revert IMetricOmmPoolActions.NotAllowedToSwap();
}
``` [3](#0-2) 

Here `msg.sender` = the pool (correct), and `sender` = whoever called `pool.swap()`. When a user calls `MetricOmmSimpleRouter.exactInputSingle()`, the router calls `pool.swap()`: [4](#0-3) 

So `sender` = router address. The check becomes `allowedSwapper[pool][router]` — it evaluates the router, not the end user.

**Step 3 — The bypass.**

A pool admin who wants to support router-mediated swaps for their allowlisted users must call `setAllowedToSwap(pool, router, true)`. Once the router is allowlisted, the check `allowedSwapper[pool][router]` returns `true` for **every** call that arrives through the router, regardless of who the original `msg.sender` to the router was. A non-allowlisted `userX` calls `router.exactInputSingle(pool, ...)` → router calls `pool.swap()` → extension sees `sender = router` → allowlist passes → swap executes.

The same bypass applies to `exactInput`, `exactOutputSingle`, and `exactOutput` multi-hop paths, all of which call `pool.swap()` with the router as `msg.sender`: [5](#0-4) 

**Contrast with `DepositAllowlistExtension`.**

The deposit allowlist correctly checks `owner` (the position owner, the economic actor), not `sender` (the immediate caller): [6](#0-5) 

The pool passes `owner` as a distinct argument from `msg.sender`, so the deposit guard is immune to the same router-mediation problem. The swap allowlist has no equivalent separation.

---

### Impact Explanation

The `SwapAllowlistExtension` is the only on-chain mechanism for a pool admin to restrict which counterparties may trade against the pool's LP liquidity. When the guard is bypassed, unauthorized users execute swaps against the pool, exposing LP principal to counterparties the pool admin explicitly intended to block. Every swap by a non-allowlisted user that should have reverted instead settles normally, draining LP value at the oracle-anchored bid/ask spread to an unauthorized party.

---

### Likelihood Explanation

The bypass requires the pool admin to have allowlisted the router address. This is not a misconfiguration — it is the only way to allow allowlisted users to use the router at all, since the router is the `sender` the pool sees. Any pool that (a) uses `SwapAllowlistExtension` and (b) wants router support is forced into this state. The trigger is a normal, unprivileged call to `MetricOmmSimpleRouter.exactInputSingle` (or any other router entry point) by any address.

---

### Recommendation

The `beforeSwap` hook should gate the **economic actor**, not the immediate caller. Two approaches:

1. **Pass the original initiator through `extensionData`**: The router encodes `msg.sender` (the end user) into `extensionData`; the extension decodes and checks it. This requires a convention between the router and the extension.

2. **Check `sender` only for direct pool calls; require the router to forward the real user**: Add a trusted-forwarder pattern where the router encodes the original caller and the extension verifies the router's identity before trusting the forwarded address.

3. **Mirror the deposit allowlist pattern**: Introduce a separate `swapper` argument (analogous to `owner` in `addLiquidity`) that the pool populates from a caller-supplied field rather than from `msg.sender`, so the router can pass the real user address.

The simplest safe fix is to document that `SwapAllowlistExtension` cannot be combined with router-mediated swaps and enforce this at the factory level (e.g., reject pool configurations that register both `SwapAllowlistExtension` and a public router as an allowed swapper).

---

### Proof of Concept

```
Setup:
  pool configured with SwapAllowlistExtension as beforeSwap hook
  admin calls setAllowedToSwap(pool, alice, true)      // alice is allowlisted
  admin calls setAllowedToSwap(pool, router, true)     // router allowlisted to support alice's router swaps
  bob is NOT individually allowlisted

Attack:
  bob calls router.exactInputSingle({pool: pool, ...})
    → router calls pool.swap(recipient, ...) with msg.sender = router
    → pool calls _beforeSwap(sender=router, ...)
    → SwapAllowlistExtension checks allowedSwapper[pool][router] → TRUE
    → swap executes for bob, who was never allowlisted

Result:
  bob swaps against the pool's LP liquidity despite being individually blocked.
  alice's individual allowlist entry is irrelevant — the router entry grants access to everyone.
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

**File:** metric-periphery/contracts/MetricOmmSimpleRouter.sol (L71-80)
```text
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
```

**File:** metric-periphery/contracts/MetricOmmSimpleRouter.sol (L99-112)
```text
    for (uint256 i = 0; i <= last; i++) {
      address pool = params.pools[i];
      bool zeroForOne = MetricOmmSwapPath.resolveZeroForOneBitmap(params.zeroForOneBitMap, i);

      _setNextCallbackContext(pool, CALLBACK_MODE_JUST_PAY, i == 0 ? msg.sender : address(this), params.tokens[i]);
      (int128 amount0Delta, int128 amount1Delta) = IMetricOmmPoolActions(pool)
        .swap(
          i == last ? params.recipient : address(this),
          zeroForOne,
          amount,
          MetricOmmSwapPath.openLimit(zeroForOne),
          "",
          params.extensionDatas[i]
        );
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
