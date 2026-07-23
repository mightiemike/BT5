### Title
SwapAllowlistExtension gates the router address instead of the real user, allowing any user to bypass per-user swap restrictions via the router - (`metric-periphery/contracts/extensions/SwapAllowlistExtension.sol`)

### Summary

`SwapAllowlistExtension.beforeSwap` checks the `sender` argument, which is the `msg.sender` of `pool.swap()`. When users go through `MetricOmmSimpleRouter`, `msg.sender` of `pool.swap()` is the **router contract**, not the actual user. This creates an irresolvable binary for pool admins: either allowlist the router (granting every user access, defeating the allowlist entirely) or do not allowlist the router (blocking all router-mediated swaps for every user, including allowlisted ones).

### Finding Description

**Hook argument binding:**

`MetricOmmPool.swap()` calls `_beforeSwap(msg.sender, recipient, ...)`: [1](#0-0) 

`ExtensionCalling._beforeSwap` forwards that value as the `sender` argument to every configured extension: [2](#0-1) 

`SwapAllowlistExtension.beforeSwap` then checks `allowedSwapper[msg.sender][sender]`, where `msg.sender` is the pool and `sender` is whoever called `pool.swap()`: [3](#0-2) 

**Router path:**

`MetricOmmSimpleRouter.exactInputSingle` calls `pool.swap(...)` directly with no user-identity forwarding: [4](#0-3) 

When this executes, `msg.sender` inside `pool.swap()` is the **router address**, so `sender` delivered to `beforeSwap` is the router, not the originating user.

**The irresolvable binary:**

| Pool admin action | Effect |
|---|---|
| Allowlist the router address | Every user can swap through the router — allowlist is fully bypassed |
| Do not allowlist the router | No user can swap through the router — allowlist blocks legitimate users |

There is no configuration that simultaneously permits router-mediated swaps for allowlisted users while blocking non-allowlisted users.

**Contrast with `DepositAllowlistExtension`:**

The deposit extension correctly gates by `owner` (the position owner explicitly passed to `addLiquidity`), which is preserved through the `MetricOmmPoolLiquidityAdder` path: [5](#0-4) 

The swap extension has no equivalent: it receives only `sender` (the direct caller of `pool.swap()`), with no separate "originating user" field.

### Impact Explanation

A curated pool deploying `SwapAllowlistExtension` to restrict which users can trade (e.g., for regulatory compliance, KYC gating, or risk management) is fully bypassed the moment the pool admin allowlists the router to enable normal UX. Any non-allowlisted user can call `MetricOmmSimpleRouter.exactInputSingle` and the extension will pass because it sees the router address, which is allowlisted. This is a direct policy bypass with fund-impacting consequences: LP assets in a curated pool are exposed to swaps from actors the pool admin explicitly intended to exclude.

### Likelihood Explanation

The router is the primary user-facing interface. Pool admins who deploy a curated pool with `SwapAllowlistExtension` will naturally allowlist the router to allow normal usage. The bypass requires no special privileges, no malicious setup, and no non-standard tokens — any user can call the public router. The vulnerability is triggered by the standard, documented usage pattern.

### Recommendation

Pass the originating user identity through the swap path so the extension can gate on the real actor. Two approaches:

1. **Add an `originator` field to the `beforeSwap` hook signature** — the pool passes `msg.sender` as `sender` (the direct caller) and a separate `originator` that the router populates (e.g., via `extensionData` or a dedicated field). The extension then checks `originator` when non-zero.

2. **Check `sender` against the router and fall back to `extensionData`** — the extension reads a user address from `extensionData` when `sender` is a known router, and checks that address against the allowlist. The router must be required to forward the real user address in `extensionData`.

The simplest safe fix is to have `SwapAllowlistExtension` check `msg.sender` (the pool) and `sender` (the direct caller), and when `sender` is a registered periphery router, require the router to embed the real user address in `extensionData` and verify that address instead.

### Proof of Concept

```
Setup:
  - Deploy pool with SwapAllowlistExtension configured
  - Pool admin calls setAllowedToSwap(pool, router, true)  // allowlist the router for normal UX
  - Pool admin does NOT call setAllowedToSwap(pool, attacker, true)

Attack:
  - attacker (not allowlisted) calls:
      router.exactInputSingle({pool: pool, ..., extensionData: ""})
  - router calls pool.swap(...) with msg.sender = router
  - pool calls _beforeSwap(sender=router, ...)
  - SwapAllowlistExtension checks allowedSwapper[pool][router] == true  → PASSES
  - attacker's swap executes on the curated pool

Result:
  - attacker bypasses the per-user allowlist entirely
  - any non-allowlisted user can trade on the curated pool via the router
``` [6](#0-5) [4](#0-3)

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

**File:** metric-core/contracts/ExtensionCalling.sol (L160-177)
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
