### Title
`SwapAllowlistExtension` Checks Router Address Instead of End-User — Any User Bypasses Curated-Pool Swap Gate via `MetricOmmSimpleRouter` - (File: `metric-periphery/contracts/extensions/SwapAllowlistExtension.sol`)

---

### Summary

`SwapAllowlistExtension.beforeSwap` receives `sender` = the direct caller of `pool.swap()`. When users route through `MetricOmmSimpleRouter`, that direct caller is the **router contract**, not the end user. A pool admin who allowlists the router (required for any router-based swap to work) simultaneously opens the gate to every user on the internet, defeating the allowlist entirely.

---

### Finding Description

In `MetricOmmPool.swap`, the pool passes `msg.sender` — its own direct caller — as the `sender` argument to `_beforeSwap`: [1](#0-0) 

`ExtensionCalling._beforeSwap` forwards that value unchanged to every configured extension: [2](#0-1) 

`SwapAllowlistExtension.beforeSwap` then gates on `allowedSwapper[msg.sender][sender]`, where `msg.sender` is the pool and `sender` is whoever called `pool.swap()`: [3](#0-2) 

When a user calls `MetricOmmSimpleRouter.exactInputSingle` (or any `exact*` variant), the router calls `pool.swap()` directly: [4](#0-3) 

At that point `msg.sender` inside the pool is the **router address**, so `sender` delivered to the extension is the router, not the end user. The actual end user's address is stored only in the transient callback context (`_getPayer()`) for payment settlement — it is never forwarded to the extension. [5](#0-4) 

This creates an inescapable dilemma for any pool admin who deploys a curated pool with `SwapAllowlistExtension`:

- **If the router is NOT allowlisted**: every allowlisted user is also blocked from using the router — the router is unusable on the pool.
- **If the router IS allowlisted** (the only way to let allowlisted users use the router): `allowedSwapper[pool][router] = true`, so the check `allowedSwapper[msg.sender][sender]` passes for **every** caller who routes through the router, including completely non-allowlisted users.

---

### Impact Explanation

Any non-allowlisted user can bypass the swap allowlist on a curated pool by calling `MetricOmmSimpleRouter.exactInputSingle` (or `exactInput` / `exactOutputSingle` / `exactOutput`). The router is a canonical, factory-validated periphery contract. Once the pool admin allowlists it — which is necessary for any router-mediated swap to work — the allowlist provides zero protection against router-routed calls. Non-allowlisted users can trade on pools intended to be restricted (e.g., KYC-gated, institution-only, or compliance-restricted pools), causing direct policy violation and potential regulatory or financial harm to the pool operator and its LPs.

---

### Likelihood Explanation

The `MetricOmmSimpleRouter` is the primary user-facing swap interface. Any pool admin who wants allowlisted users to be able to use the standard router **must** allowlist the router address. This is a normal, expected operational step. Once taken, the bypass is trivially reachable by any user with no special privileges, no flash loan, and no multi-transaction setup — a single `exactInputSingle` call suffices.

---

### Recommendation

The extension must gate on the **economically relevant actor** — the end user — not the intermediary. Two complementary fixes:

1. **Pass the original `msg.sender` through the router**: The router already stores the true payer in transient storage. Thread it through `pool.swap` as an additional `originator` argument (or encode it in `extensionData`) so extensions can check it.

2. **Alternatively, check `sender` in the extension against a separate router-aware mapping**: The extension could maintain a `trustedRouter` set and, when `sender` is a trusted router, require the extension payload to carry the real user address (signed or encoded by the router).

The simplest correct fix is option 1: add an `originator` field to the swap call that the pool always sets to `msg.sender` at the outermost entry, and have the router forward `msg.sender` (the end user) in that field rather than letting the pool use the router's address.

---

### Proof of Concept

```
Setup:
  pool configured with SwapAllowlistExtension
  allowedSwapper[pool][alice]  = true   // alice is KYC'd
  allowedSwapper[pool][router] = true   // admin must set this for alice to use the router

Attack (bob, not allowlisted):
  bob calls router.exactInputSingle({pool: pool, ...})
  router calls pool.swap(recipient, zeroForOne, amount, limit, "", extensionData)
    → msg.sender inside pool = router
    → _beforeSwap(sender=router, ...)
    → SwapAllowlistExtension.beforeSwap(sender=router, ...)
    → allowedSwapper[pool][router] == true  ✓  (check passes)
  swap executes — bob trades on a pool he should be barred from
```

Direct pool call by bob (no router):
```
  bob calls pool.swap(...)
    → msg.sender inside pool = bob
    → allowedSwapper[pool][bob] == false  ✗  (correctly reverts)
```

The bypass is exclusive to the router path and requires zero privileges beyond a standard ERC-20 approval.

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
