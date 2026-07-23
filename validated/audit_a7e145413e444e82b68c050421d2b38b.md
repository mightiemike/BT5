### Title
`SwapAllowlistExtension` Checks Router Address Instead of End User — Any User Bypasses Curated-Pool Swap Gate via `MetricOmmSimpleRouter` - (File: `metric-periphery/contracts/extensions/SwapAllowlistExtension.sol`)

---

### Summary

`SwapAllowlistExtension.beforeSwap` gates swaps by checking the `sender` argument against a per-pool allowlist. The pool passes `msg.sender` of the `swap()` call as `sender`. When a user routes through `MetricOmmSimpleRouter`, `msg.sender` to the pool is the **router contract**, not the end user. The extension therefore checks the router's address, not the actual swapper's address. A pool admin who allowlists the router to enable any router-mediated swap simultaneously grants every user — including non-allowlisted ones — the ability to bypass the individual allowlist entirely.

---

### Finding Description

`MetricOmmPool.swap()` passes `msg.sender` as the `sender` argument to `_beforeSwap`, which forwards it verbatim to every configured extension: [1](#0-0) 

`ExtensionCalling._beforeSwap` encodes that `sender` into the call to the extension: [2](#0-1) 

`SwapAllowlistExtension.beforeSwap` then checks `allowedSwapper[msg.sender][sender]`, where `msg.sender` is the pool and `sender` is whoever called `pool.swap()`: [3](#0-2) 

`MetricOmmSimpleRouter.exactInputSingle` calls `pool.swap()` directly, making the router the `msg.sender` to the pool: [4](#0-3) 

The same applies to `exactInput` (all hops), `exactOutputSingle`, and `exactOutput` (all hops including recursive callback hops): [5](#0-4) [6](#0-5) 

The pool admin is therefore forced into an impossible choice:

- **Do not allowlist the router** → every allowlisted user is also blocked from using the router (router-mediated swaps revert for everyone).
- **Allowlist the router** → the extension checks `allowedSwapper[pool][router]`, which passes for every caller regardless of whether the actual end user is on the allowlist.

There is no configuration that achieves "only allowlisted users may swap, whether directly or through the router."

---

### Impact Explanation

Pools deploying `SwapAllowlistExtension` are curated pools — KYC-gated, institutional, or otherwise restricted. A non-allowlisted user who routes through `MetricOmmSimpleRouter` can execute swaps on such a pool whenever the router is allowlisted. This allows unauthorized actors to trade against LP liquidity that the pool admin intended to restrict, directly exposing LP principal to trades from actors the allowlist was designed to exclude.

---

### Likelihood Explanation

The router is the standard, documented periphery entry point for end users. Any pool that wants to support router-mediated swaps for its allowlisted users must allowlist the router, at which point the bypass is unconditional and requires no special setup from the attacker — only a standard `exactInputSingle` call through the router.

---

### Recommendation

The extension must gate on the **economic actor**, not the intermediary. Two sound approaches:

1. **Pass the original `msg.sender` through the router.** Add an `originalSender` field to `extensionData` that the router populates with `msg.sender` before calling the pool. The extension decodes and checks that field instead of (or in addition to) `sender`. The pool's `onlyPool` guard already ensures only a registered pool can call the extension, so the extension can trust the decoded value if the pool is trusted.

2. **Check `sender` and `recipient` together.** Allowlist the router as a pass-through intermediary and require the extension to also check `recipient` (the actual beneficiary of the swap output). This is weaker but avoids breaking the router flow.

Option 1 is the correct fix. The router already stores `msg.sender` in transient storage for callback payment; the same value should be forwarded as part of `extensionData` so guards can key on the true originator.

---

### Proof of Concept

1. Pool admin deploys a pool with `SwapAllowlistExtension` configured as a `beforeSwap` hook.
2. Admin allowlists `userA` via `setAllowedToSwap(pool, userA, true)`.
3. Admin also allowlists the router via `setAllowedToSwap(pool, router, true)` so that `userA` can use the router.
4. `userB` (not allowlisted) calls `MetricOmmSimpleRouter.exactInputSingle(pool, ...)`.
5. The router calls `pool.swap(...)` with `msg.sender = router`.
6. The pool calls `extension.beforeSwap(router, ...)`.
7. The extension checks `allowedSwapper[pool][router]` → `true` → swap proceeds.
8. `userB` successfully swaps on a pool that was supposed to block them, trading against LP liquidity without authorization.

If the admin does **not** allowlist the router (step 3 omitted), then step 6 checks `allowedSwapper[pool][router]` → `false` → `userA`'s router swap also reverts, making the router unusable for all users including allowlisted ones.

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

**File:** metric-periphery/contracts/MetricOmmSimpleRouter.sol (L220-228)
```text
    (int128 amount0DeltaReturned, int128 amount1DeltaReturned) = IMetricOmmPoolActions(pool)
      .swap(
        msg.sender,
        zeroForOne,
        MetricOmmSwapInputs.asAmountSpecifiedFromPositive(amountToPay),
        MetricOmmSwapPath.openLimit(zeroForOne),
        data,
        cb.extensionDatas[tradesLeft]
      );
```
