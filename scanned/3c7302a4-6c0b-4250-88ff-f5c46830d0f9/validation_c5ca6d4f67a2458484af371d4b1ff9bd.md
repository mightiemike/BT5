### Title
SwapAllowlistExtension gates the router address instead of the actual user, enabling full allowlist bypass via MetricOmmSimpleRouter — (`metric-periphery/contracts/extensions/SwapAllowlistExtension.sol`)

---

### Summary

`SwapAllowlistExtension.beforeSwap` checks the `sender` argument, which the pool sets to `msg.sender` of the `swap()` call. When a user routes through `MetricOmmSimpleRouter`, `msg.sender` at the pool is the **router contract**, not the end user. If the pool admin allowlists the router to enable router-mediated swaps for curated users, every unprivileged address can bypass the allowlist by calling the router.

---

### Finding Description

`MetricOmmPool.swap()` calls `_beforeSwap` with `msg.sender` as the `sender` argument:

```solidity
// MetricOmmPool.sol line 230-240
_beforeSwap(
    msg.sender,   // ← whoever called pool.swap()
    recipient,
    ...
);
```

`ExtensionCalling._beforeSwap` forwards that value verbatim to the extension:

```solidity
// ExtensionCalling.sol line 162-176
abi.encodeCall(
    IMetricOmmExtensions.beforeSwap,
    (sender, recipient, ...)
)
```

`SwapAllowlistExtension.beforeSwap` then checks:

```solidity
// SwapAllowlistExtension.sol line 37-38
if (!allowAllSwappers[msg.sender] && !allowedSwapper[msg.sender][sender]) {
    revert IMetricOmmPoolActions.NotAllowedToSwap();
}
```

Here `msg.sender` is the pool (correct key) and `sender` is whoever called `pool.swap()`.

`MetricOmmSimpleRouter.exactInputSingle` calls `pool.swap()` directly:

```solidity
// MetricOmmSimpleRouter.sol line 72-80
(int128 amount0Delta, int128 amount1Delta) = IMetricOmmPoolActions(params.pool)
    .swap(
        params.recipient,
        params.zeroForOne,
        ...
        params.extensionData
    );
```

So `msg.sender` inside `pool.swap()` is the **router address**, not the end user. The extension therefore evaluates `allowedSwapper[pool][router]`.

**Bypass path:** The pool admin wants KYC'd users to be able to use the router. They allowlist the router: `setAllowedToSwap(pool, router, true)`. From that moment, any address — including non-KYC'd users — can call `exactInputSingle` / `exactInput` / `exactOutputSingle` / `exactOutput` on the router and the allowlist check passes unconditionally, because the check resolves to `allowedSwapper[pool][router] == true`.

The same issue applies to the multi-hop `exactInput` path where intermediate hops use `address(this)` (the router) as the effective sender, and to the recursive `exactOutput` path.

---

### Impact Explanation

A curated pool deploying `SwapAllowlistExtension` to restrict trading to a specific set of addresses (e.g., KYC'd counterparties, whitelisted market makers) loses that protection entirely once the router is allowlisted. Any unprivileged address can execute swaps against the pool, draining LP value at oracle-derived prices without the intended access control. This is a direct loss of the policy guarantee the pool admin configured, and any LP who deposited under the assumption that only allowlisted parties could trade faces unintended counterparty exposure.

---

### Likelihood Explanation

The scenario is reachable through normal, non-malicious pool administration. A pool admin who wants allowlisted users to be able to use the standard periphery router must allowlist the router — there is no other mechanism. The admin has no way to know that doing so opens the gate to all users, because the extension's parameter name (`sender`) implies it checks the economic actor, not the intermediary. The router is a public, factory-verified contract, so the bypass requires no special privileges beyond calling a public function.

---

### Recommendation

The extension must resolve the original end-user identity rather than the immediate `msg.sender` of `pool.swap()`. Two complementary approaches:

1. **Pass the original caller through `extensionData`**: The router encodes `msg.sender` into `extensionData`; the extension decodes and verifies it. This requires a trusted encoding convention between the router and the extension.

2. **Check `sender` against the allowlist; if `sender` is the router, decode the real user from `extensionData`**: The extension can detect known router addresses and fall back to an encoded identity claim, verified by a signature or by the router's own transient storage.

3. **Structural fix**: Add a `realSender` field to the `beforeSwap` hook arguments that the pool populates from a trusted transient context set by the router before calling `pool.swap()`, similar to how the router already stores the payer in transient storage for the callback.

---

### Proof of Concept

```
Setup:
  pool = MetricOmmPool with SwapAllowlistExtension configured
  allowedSwapper[pool][alice] = true   // alice is KYC'd
  allowedSwapper[pool][router] = true  // admin enables router for alice

Attack (by bob, not allowlisted):
  bob calls MetricOmmSimpleRouter.exactInputSingle({pool: pool, ...})
  → router calls pool.swap(...)
  → pool calls _beforeSwap(msg.sender=router, ...)
  → SwapAllowlistExtension checks allowedSwapper[pool][router] == true  ✓
  → swap executes for bob with no allowlist enforcement
```

The check that should have been `allowedSwapper[pool][bob]` (which is `false`) instead resolves to `allowedSwapper[pool][router]` (which is `true`), granting bob full swap access. [1](#0-0) [2](#0-1) [3](#0-2) [4](#0-3) [5](#0-4)

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

**File:** metric-core/contracts/ExtensionCalling.sol (L159-177)
```text
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

**File:** metric-periphery/contracts/MetricOmmSimpleRouter.sol (L99-118)
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

      int128 amountInActual = MetricOmmSwapResults.extractAmountIn(zeroForOne, amount0Delta, amount1Delta);
      if (amountInActual < amount) revert InvalidInputAmountAtHop(uint8(i), amountInActual, amount);

      amount = MetricOmmSwapResults.extractAmountOut(zeroForOne, amount0Delta, amount1Delta);
    }
```
