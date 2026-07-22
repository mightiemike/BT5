The call chain is fully traceable. Here is the definitive assessment.

---

### Title
Router-Mediated Swaps Bypass `SwapAllowlistExtension` — (`metric-periphery/contracts/extensions/SwapAllowlistExtension.sol`)

### Summary

`SwapAllowlistExtension::beforeSwap` gates swaps by checking `allowedSwapper[pool][sender]`, where `sender` is the pool's `msg.sender`. When a swap is routed through `MetricOmmSimpleRouter`, the pool's `msg.sender` is the **router contract**, not the original user. The hook therefore checks whether the router is allowlisted, not whether the actual end-user is allowlisted. Any unprivileged user can bypass a per-address swap allowlist by routing through the public router.

---

### Finding Description

**Step 1 — Pool passes its own `msg.sender` as `sender` to the hook.**

In `MetricOmmPool::swap`, the pool calls:

```solidity
_beforeSwap(msg.sender, recipient, ...)
``` [1](#0-0) 

`ExtensionCalling::_beforeSwap` then encodes that value as the first argument to the extension call:

```solidity
abi.encodeCall(IMetricOmmExtensions.beforeSwap, (sender, recipient, ...))
``` [2](#0-1) 

**Step 2 — The router is the pool's `msg.sender`.**

`MetricOmmSimpleRouter::exactInputSingle` calls `pool.swap(...)` directly:

```solidity
IMetricOmmPoolActions(params.pool).swap(params.recipient, ...)
``` [3](#0-2) 

So from the pool's perspective, `msg.sender` = router address. The same applies to `exactInput`, `exactOutputSingle`, and `exactOutput`. [4](#0-3) 

**Step 3 — The hook checks the router's address, not the user's.**

```solidity
function beforeSwap(address sender, ...) external view override returns (bytes4) {
    if (!allowAllSwappers[msg.sender] && !allowedSwapper[msg.sender][sender]) {
        revert IMetricOmmPoolActions.NotAllowedToSwap();
    }
    ...
}
``` [5](#0-4) 

Here `msg.sender` = pool (correct key), but `sender` = router address (not the original user). The lookup `allowedSwapper[pool][router]` is evaluated, not `allowedSwapper[pool][actual_user]`.

**The two broken outcomes:**

| Router allowlisted? | Result |
|---|---|
| No | All router-mediated swaps revert, even for allowlisted users — core functionality broken |
| Yes | Every unprivileged user bypasses the allowlist by routing through the public router |

---

### Impact Explanation

The `SwapAllowlistExtension` is the only mechanism to restrict who may swap in a pool. A pool admin who configures it to allow only specific addresses (e.g., KYC'd market makers, whitelisted counterparties) gets no protection against any user who calls `MetricOmmSimpleRouter`. The router is a public, permissionless contract. The allowlist is completely defeated for the router path, which is the primary user-facing swap entrypoint.

---

### Likelihood Explanation

High. The router is the standard swap entrypoint for end users. Any user who discovers the allowlist can trivially route through the router. No privileged access, no special token behavior, and no oracle manipulation is required.

---

### Recommendation

Pass `tx.origin` or, preferably, a caller-supplied identity through the extension data, and validate it in the hook. A cleaner approach: add a `swapper` parameter to the router's `exact*` functions that is forwarded in `extensionData`, and have `SwapAllowlistExtension::beforeSwap` decode and check that value instead of (or in addition to) `sender`. Alternatively, document that pools using `SwapAllowlistExtension` must allowlist the router and accept that the router path is open to all.

---

### Proof of Concept

1. Deploy a pool with `SwapAllowlistExtension` configured. Call `setAllowedToSwap(pool, alice, true)`. Do **not** allowlist the router or `allowAllSwappers`.
2. Have `bob` (not allowlisted) call `MetricOmmSimpleRouter.exactInputSingle(...)` targeting the pool.
3. The pool calls `beforeSwap(router_address, ...)`. The hook evaluates `allowedSwapper[pool][router_address]` → `false`. The swap reverts — confirming the router is not allowlisted.
4. Now call `setAllowedToSwap(pool, router, true)` (the only way to make router swaps work).
5. Have `bob` call `MetricOmmSimpleRouter.exactInputSingle(...)` again. The hook evaluates `allowedSwapper[pool][router]` → `true`. Bob's swap succeeds despite not being allowlisted.
6. Assert: `alice` (the intended allowlisted user) and `bob` (an unprivileged user) are indistinguishable to the hook when both route through the router.

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

**File:** metric-core/contracts/ExtensionCalling.sol (L160-176)
```text
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
```

**File:** metric-periphery/contracts/MetricOmmSimpleRouter.sol (L72-80)
```text
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

**File:** metric-periphery/contracts/MetricOmmSimpleRouter.sol (L103-112)
```text
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
