### Title
SwapAllowlistExtension Gates the Router Address Instead of the Actual User, Rendering the Swap Allowlist Broken — (`metric-periphery/contracts/extensions/SwapAllowlistExtension.sol`)

---

### Summary

`SwapAllowlistExtension.beforeSwap` checks the `sender` argument against the per-pool allowlist. When a user swaps through `MetricOmmSimpleRouter`, the pool's `swap()` receives `msg.sender = router`, so the extension checks the **router's address**, not the actual user's address. The allowlist therefore either blocks every allowlisted user (if the router is not allowlisted) or is completely bypassed for every user (if the router is allowlisted).

---

### Finding Description

**Call path:**

```
User EOA
  → MetricOmmSimpleRouter.exactInputSingle()          [msg.sender = User]
      → IMetricOmmPoolActions(pool).swap(recipient, …) [msg.sender = Router]
          → MetricOmmPool._beforeSwap(msg.sender, …)   [sender = Router]
              → SwapAllowlistExtension.beforeSwap(sender=Router, …)
                  → allowedSwapper[pool][Router]  ← WRONG identity checked
```

In `MetricOmmPool.swap`, the pool passes its own `msg.sender` as the `sender` argument to `_beforeSwap`: [1](#0-0) 

`ExtensionCalling._beforeSwap` forwards that value unchanged to every configured extension: [2](#0-1) 

`SwapAllowlistExtension.beforeSwap` then checks `allowedSwapper[msg.sender][sender]`, where `msg.sender` is the pool and `sender` is whatever called the pool — the router, not the user: [3](#0-2) 

The router never injects the originating user's address into the pool call; it simply calls `pool.swap()` directly: [4](#0-3) 

---

### Impact Explanation

Two mutually exclusive failure modes arise for any pool that configures `SwapAllowlistExtension`:

**Mode A — Allowlisted users are blocked (funds frozen in pool):**
The pool admin allowlists specific user EOAs (e.g., KYC-verified addresses). Those users call `exactInputSingle` through the router. The extension checks `allowedSwapper[pool][router]`, which is `false`. Every swap reverts with `NotAllowedToSwap`. Because EOAs cannot implement `IMetricOmmSwapCallback`, they cannot call `pool.swap()` directly either. The allowlisted users are permanently unable to swap out of the pool — their token positions are frozen.

**Mode B — Allowlist is completely bypassed:**
To let any user swap through the router, the admin must allowlist the router address. This grants every user — including those the allowlist was meant to exclude — unrestricted swap access, defeating the entire access-control mechanism.

Both outcomes represent broken core pool functionality: either legitimate users lose access to the swap path, or the allowlist guard is rendered inoperative.

---

### Likelihood Explanation

Any pool that deploys `SwapAllowlistExtension` and expects to gate individual user addresses is affected by construction. The router is the standard, documented user-facing entry point for swaps. No special attacker action is required — a normal `exactInputSingle` call through the router is sufficient to trigger either failure mode. The pool admin's configuration is correct; the bug is structural in the extension's identity check.

---

### Recommendation

The extension must check the **originating user**, not the intermediary. Two complementary fixes:

1. **Pass the payer/originator through the router.** The router already stores the originating `msg.sender` in transient storage (`_setNextCallbackContext(…, msg.sender, …)`). Forward it as part of `extensionData` or a dedicated field so the extension can recover it.

2. **Check `sender` only for direct pool calls; recover the real user from `extensionData` for router-mediated calls.** Alternatively, redesign `beforeSwap` to accept and verify a signed or transient-storage-backed user identity rather than relying on the raw `sender` argument.

The `DepositAllowlistExtension` correctly gates `owner` (the position owner, not the caller), demonstrating the intended pattern: [5](#0-4) 

The swap extension should adopt the same approach — gate the economically relevant actor, not the intermediary.

---

### Proof of Concept

1. Pool admin deploys a pool with `SwapAllowlistExtension` configured as `beforeSwap` extension.
2. Admin calls `setAllowedToSwap(pool, userEOA, true)` — intending to allow `userEOA` to swap.
3. `userEOA` calls `MetricOmmSimpleRouter.exactInputSingle({pool: pool, …})`.
4. Router calls `pool.swap(recipient, …)` — pool sees `msg.sender = router`.
5. Pool calls `_beforeSwap(router, …)`.
6. Extension evaluates `allowedSwapper[pool][router]` → `false`.
7. Transaction reverts with `NotAllowedToSwap`.
8. `userEOA` cannot swap despite being explicitly allowlisted. Their token position in the pool is inaccessible via the swap path.

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
