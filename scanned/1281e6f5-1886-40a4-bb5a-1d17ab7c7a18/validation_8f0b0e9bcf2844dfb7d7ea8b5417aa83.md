### Title
`SwapAllowlistExtension` Checks Router Address Instead of Actual Swapper, Allowing Any User to Bypass Per-User Swap Allowlist — (`metric-periphery/contracts/extensions/SwapAllowlistExtension.sol`)

---

### Summary

`SwapAllowlistExtension.beforeSwap` gates on the `sender` argument, which the pool sets to `msg.sender` of the `swap()` call. When a user routes through `MetricOmmSimpleRouter`, the router is `msg.sender` to the pool, so the extension checks whether the **router address** is allowlisted — not the actual end user. A pool admin who allowlists the router to enable router-mediated swaps for legitimate users inadvertently opens the gate to every user on-chain.

---

### Finding Description

**Actor binding in `SwapAllowlistExtension`:** [1](#0-0) 

The check is `allowedSwapper[msg.sender][sender]` where `msg.sender` is the pool (correct) and `sender` is whoever called `pool.swap()`.

**Pool passes `msg.sender` as `sender` to the hook:** [2](#0-1) 

**Router calls `pool.swap()` directly — no original-user forwarding:** [3](#0-2) 

The router stores the original `msg.sender` only in transient storage for the payment callback (`_setNextCallbackContext(params.pool, CALLBACK_MODE_JUST_PAY, msg.sender, params.tokenIn)`), but never passes it to the pool's `swap()` call. The pool therefore sees `msg.sender = router`, and the extension receives `sender = router`.

**Consequence:** The allowlist lookup becomes `allowedSwapper[pool][router]`. If the pool admin allowlists the router address (required for any router-mediated swap to succeed for legitimate users), the check passes for **every** caller who routes through the router, regardless of whether that caller is individually allowlisted.

The `DepositAllowlistExtension` does not share this flaw — it correctly gates on `owner` (the position owner), which is explicitly passed by the caller and is not overwritten by the router: [4](#0-3) 

---

### Impact Explanation

A curated pool deploying `SwapAllowlistExtension` to restrict trading to KYC'd or otherwise approved addresses is fully bypassed once the router is allowlisted. Any unprivileged user can call `MetricOmmSimpleRouter.exactInputSingle` (or any other router entry point) and trade on the pool as if they were allowlisted. This is a direct policy bypass with fund-impacting consequences: the pool's LP assets are exposed to swaps from actors the pool admin explicitly intended to exclude, violating the core invariant that "a curated pool must enforce the same allowlist policy regardless of which supported public entrypoint reaches it."

---

### Likelihood Explanation

The `MetricOmmSimpleRouter` is the standard, documented periphery swap path. Any pool admin who wants allowlisted users to be able to use the router (the normal UX) must allowlist the router address. The moment they do, the per-user gate collapses. The attacker needs no special privilege — only the ability to call the public router.

---

### Recommendation

The extension must resolve the actual end-user identity, not the intermediary. Two viable approaches:

1. **Pass original caller via `extensionData`:** The router encodes `msg.sender` into `extensionData` before forwarding to the pool. The `SwapAllowlistExtension` decodes and verifies this value. This requires a trust assumption that the router is the only allowed intermediary (enforced by also checking `sender == router`).

2. **Gate on `recipient` instead of `sender`:** If the pool's design intent is that the economic beneficiary of the swap is the controlled party, check `recipient`. This is already available in the `beforeSwap` signature. However, this only works if the pool admin's policy is to gate who *receives* output, not who *initiates* the swap.

The cleanest fix is option 1: the extension checks `sender` is the allowlisted router, then decodes and verifies the embedded original caller from `extensionData`.

---

### Proof of Concept

```
Setup:
  - Deploy pool with SwapAllowlistExtension
  - Pool admin calls setAllowedToSwap(pool, userA, true)       // allowlist userA
  - Pool admin calls setAllowedToSwap(pool, router, true)      // required for router-mediated swaps

Attack:
  - userB (not allowlisted) calls:
      router.exactInputSingle({pool: pool, ..., recipient: userB})
  - Router calls pool.swap(userB, ...) with msg.sender = router
  - Pool calls _beforeSwap(sender=router, ...)
  - SwapAllowlistExtension checks allowedSwapper[pool][router] → true
  - Swap executes for userB despite userB not being allowlisted

Result:
  - userB successfully swaps on a curated pool
  - The per-user allowlist is completely bypassed via the public router
``` [5](#0-4) [6](#0-5) [3](#0-2)

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
