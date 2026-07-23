### Title
`SwapAllowlistExtension` Checks Router Address Instead of Actual User, Enabling Allowlist Bypass or Blocking Allowlisted Users from the Router Path — (`metric-periphery/contracts/extensions/SwapAllowlistExtension.sol`)

---

### Summary

`SwapAllowlistExtension.beforeSwap` gates swaps by checking `allowedSwapper[pool][sender]`, where `sender` is `msg.sender` of the pool's `swap()` call. When users route through `MetricOmmSimpleRouter`, `sender` is the **router address**, not the actual end user. This produces two fund-impacting outcomes: (1) if the router is allowlisted, any unprivileged user bypasses the curation gate entirely; (2) if only specific EOAs are allowlisted, those users cannot use the standard router interface and are silently locked out of the swap flow.

---

### Finding Description

`MetricOmmPool.swap` passes `msg.sender` as the `sender` argument to `_beforeSwap`: [1](#0-0) 

`ExtensionCalling._beforeSwap` forwards that value unchanged as the first argument to every configured extension: [2](#0-1) 

`SwapAllowlistExtension.beforeSwap` then checks that `sender` value against the per-pool allowlist: [3](#0-2) 

When `MetricOmmSimpleRouter.exactInputSingle` calls the pool, the pool's `msg.sender` is the **router contract**, not the end user: [4](#0-3) 

So the extension evaluates `allowedSwapper[pool][router]` — the router's address — rather than the actual user's address. The same applies to `exactInput`, `exactOutputSingle`, and `exactOutput`.

**Contrast with `DepositAllowlistExtension`**, which correctly ignores `sender` and checks `owner` (the LP position owner explicitly passed by the caller): [5](#0-4) 

The deposit extension works correctly through `MetricOmmPoolLiquidityAdder` because `owner` is an explicit parameter that the adder passes through unchanged: [6](#0-5) 

The swap extension has no equivalent mechanism — the pool's `swap()` interface does not accept a separate "actual user" parameter, so the extension can only see the direct caller.

---

### Impact Explanation

**Path A — Allowlist bypass (High):** A pool admin configures `SwapAllowlistExtension` to restrict swaps to KYC'd or institutional addresses. The admin also allowlists `MetricOmmSimpleRouter` as a trusted periphery contract (a natural assumption since it is the protocol's own router). Any unprivileged user can now call `router.exactInputSingle(...)` and the extension evaluates `allowedSwapper[pool][router] == true` → swap proceeds. The pool's entire curation policy is nullified. Unauthorized users trade on a pool that was designed to exclude them, breaking the admin-boundary invariant.

**Path B — Broken swap flow for allowlisted users (Medium):** A pool admin allowlists specific EOA addresses but does not allowlist the router. Those EOAs attempt to use the standard router interface. The extension evaluates `allowedSwapper[pool][router] == false` → `NotAllowedToSwap` revert. The allowlisted users cannot use the protocol's own router and must implement the raw `IMetricOmmSwapCallback` interface themselves to call the pool directly — a non-trivial requirement that effectively makes the swap flow unusable for the intended audience.

Both outcomes violate the invariant: *a curated pool must enforce the same allowlist policy regardless of which supported public entrypoint reaches it.*

---

### Likelihood Explanation

- Any pool that deploys `SwapAllowlistExtension` is affected.
- Path A requires the admin to allowlist the router, which is a natural operational mistake given that the router is the protocol's own trusted periphery.
- Path B requires no admin mistake — it is the default outcome whenever specific EOAs are allowlisted without also allowlisting the router.
- No special capital, permissions, or mempool access is required; any user can trigger Path A by calling the public router.

---

### Recommendation

The `beforeSwap` hook should gate on the economically relevant actor. Two options:

**Option 1 (preferred):** Mirror the deposit extension pattern. Have the pool pass the actual initiating user through `extensionData` or a dedicated field, and have the extension decode it. Alternatively, restructure `beforeSwap` to accept a separate `payer`/`initiator` argument that the router populates.

**Option 2:** Check `tx.origin` as a fallback when `sender` is a known router. This is generally discouraged but is used in some allowlist contexts.

**Option 3:** Document that `SwapAllowlistExtension` only works for direct pool calls and provide a separate router-aware allowlist extension that decodes the actual user from `extensionData`.

At minimum, `SwapAllowlistExtension` should carry a NatSpec warning that it does not correctly gate router-mediated swaps.

---

### Proof of Concept

```
Setup:
  pool = MetricOmmPool with SwapAllowlistExtension configured
  admin allowlists: router = MetricOmmSimpleRouter (trusted periphery)
  admin does NOT allowlist: attacker EOA

Attack (Path A — bypass):
  1. attacker calls router.exactInputSingle({pool: pool, tokenIn: T0, ...})
  2. router calls pool.swap(recipient, zeroForOne, amount, ...)
     → pool's msg.sender = router
  3. pool calls _beforeSwap(sender=router, ...)
  4. ExtensionCalling dispatches to SwapAllowlistExtension.beforeSwap(sender=router, ...)
  5. extension checks: allowedSwapper[pool][router] == true  ← router is allowlisted
  6. extension returns selector → swap proceeds
  7. attacker (not in allowlist) successfully swaps on a restricted pool

Attack (Path B — DoS of allowlisted user):
  Setup: admin allowlists alice EOA, does NOT allowlist router
  1. alice calls router.exactInputSingle({pool: pool, ...})
  2. router calls pool.swap(...)  → pool's msg.sender = router
  3. extension checks: allowedSwapper[pool][router] == false
  4. extension reverts NotAllowedToSwap
  5. alice (explicitly allowlisted) cannot use the router
  6. alice must implement IMetricOmmSwapCallback directly to call pool.swap() herself
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

**File:** metric-periphery/contracts/MetricOmmPoolLiquidityAdder.sol (L194-196)
```text
    try IMetricOmmPoolActions(pool)
      .addLiquidity(positionOwner, salt, deltas, abi.encode(KIND_PAY), extensionData) returns (
      uint256 a0, uint256 a1
```
