### Title
SwapAllowlistExtension gates the router address instead of the end user, allowing any user to bypass the swap allowlist — (`metric-periphery/contracts/extensions/SwapAllowlistExtension.sol`)

---

### Summary

`SwapAllowlistExtension.beforeSwap` checks `allowedSwapper[msg.sender][sender]`, where `sender` is the address the pool received as `msg.sender` when `swap` was called. When a user routes through `MetricOmmSimpleRouter`, the pool's `msg.sender` is the router, so the extension checks whether the **router** is allowlisted, not the actual end user. Any user can bypass a pool's swap allowlist by calling the public router instead of the pool directly.

---

### Finding Description

`MetricOmmPool.swap` passes `msg.sender` as the `sender` argument to `_beforeSwap`, which forwards it verbatim to every configured extension: [1](#0-0) 

`ExtensionCalling._beforeSwap` encodes that `sender` into the `beforeSwap` call payload: [2](#0-1) 

`SwapAllowlistExtension.beforeSwap` then uses `msg.sender` (the pool) as the mapping key and `sender` (the immediate caller of `pool.swap`) as the identity to gate: [3](#0-2) 

When a user calls `MetricOmmSimpleRouter.exactInputSingle`, the router calls `pool.swap(...)` directly: [4](#0-3) 

At that point `msg.sender` inside the pool is the router contract, so `sender` forwarded to the extension is the router address. The extension evaluates `allowedSwapper[pool][router]` — not `allowedSwapper[pool][actual_user]`. The actual user's allowlist entry is never consulted.

This is structurally identical to the MeterCap seed bug: a capability check is keyed on the wrong identity (the intermediary/router rather than the principal), so the guard is trivially bypassed by routing through the intermediary.

---

### Impact Explanation

A pool admin who deploys `SwapAllowlistExtension` to restrict swaps to a known set of addresses (e.g., KYC-verified counterparties, whitelisted market makers, or a private trading pool) has that restriction completely defeated. Any unprivileged user can call `MetricOmmSimpleRouter.exactInputSingle` (or `exactInput` / `exactOutputSingle` / `exactOutput`) and the extension will check the router's allowlist status, not theirs. If the router is allowlisted (the natural operational assumption for a public router), every user on the network can swap. If the router is not allowlisted, every user who legitimately uses the router is blocked even if they are individually allowlisted — making the pool unusable through the standard periphery path.

The admin-configured guard is bypassed by an unprivileged path (the public router), which falls squarely within the "admin-boundary break" allowed impact class.

---

### Likelihood Explanation

- The `MetricOmmSimpleRouter` is the standard, public, documented entry point for swaps.
- No special privilege, token, or setup is required — any EOA can call `exactInputSingle`.
- The bypass is automatic and unconditional whenever the router is the pool's `msg.sender`.
- Pool admins have no on-chain mechanism to distinguish router-mediated swaps from direct swaps within the current extension interface.

---

### Recommendation

The extension must gate the **end user**, not the immediate caller of `pool.swap`. Two complementary fixes:

1. **Pass the original initiator through the router.** The router already stores `msg.sender` in transient storage as the payer. Extend the `extensionData` or a dedicated transient slot so the pool or extension can recover the true initiator. The extension then reads that value instead of `sender`.

2. **Alternatively, check `sender` only when `sender` is not a known router.** The extension could maintain a registry of trusted routers and, when `sender` is a router, require the router to attest the real user inside `extensionData`.

The simplest safe fix is option 1: the router encodes `msg.sender` into `extensionData`, and `SwapAllowlistExtension.beforeSwap` decodes and checks that address when `sender` is a recognized router address.

---

### Proof of Concept

```
Setup:
  1. Deploy a pool with SwapAllowlistExtension configured.
  2. Pool admin calls setAllowedToSwap(pool, alice, true)  — only alice is allowed.
  3. Pool admin calls setAllowedToSwap(pool, router, true) — router is allowlisted for operational use.

Attack:
  4. Bob (not allowlisted) calls MetricOmmSimpleRouter.exactInputSingle({pool: pool, ...}).
  5. Router calls pool.swap(recipient, ...) — pool's msg.sender = router.
  6. Pool calls extension.beforeSwap(sender=router, ...).
  7. Extension checks allowedSwapper[pool][router] == true  → passes.
  8. Bob's swap executes successfully despite not being on the allowlist.

Result:
  allowedSwapper[pool][bob] is never checked; the allowlist is fully bypassed.
``` [5](#0-4) [1](#0-0) [4](#0-3)

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
