### Title
`SwapAllowlistExtension` Checks Router Address Instead of Actual User, Allowing Any User to Bypass the Swap Allowlist via `MetricOmmSimpleRouter` — (`metric-periphery/contracts/extensions/SwapAllowlistExtension.sol`)

---

### Summary

`SwapAllowlistExtension.beforeSwap` gates swaps by checking the `sender` argument passed by the pool. Because `MetricOmmPool.swap` always passes `msg.sender` as `sender`, and `MetricOmmSimpleRouter` is the direct caller of `pool.swap`, the extension sees the router's address — not the actual end user's address. A pool admin who adds the router to the allowlist (the only way to support router-mediated swaps for their intended users) inadvertently opens the pool to every user who can call the router, completely defeating the per-user curation the extension is meant to enforce.

---

### Finding Description

**Step 1 — Pool passes `msg.sender` as `sender` to extensions.**

In `MetricOmmPool.swap`, the pool calls `_beforeSwap` with `msg.sender` as the first argument:

```solidity
// metric-core/contracts/MetricOmmPool.sol  line 230-240
_beforeSwap(
    msg.sender,   // ← always the direct caller of pool.swap()
    recipient,
    ...
);
``` [1](#0-0) 

**Step 2 — `ExtensionCalling._beforeSwap` forwards that value unchanged.**

```solidity
// metric-core/contracts/ExtensionCalling.sol  line 160-176
_callExtensionsInOrder(
    BEFORE_SWAP_ORDER,
    abi.encodeCall(
        IMetricOmmExtensions.beforeSwap,
        (sender, recipient, ...)   // sender = msg.sender of pool.swap()
    )
);
``` [2](#0-1) 

**Step 3 — `SwapAllowlistExtension.beforeSwap` checks that forwarded `sender`.**

```solidity
// metric-periphery/contracts/extensions/SwapAllowlistExtension.sol  line 31-41
function beforeSwap(address sender, address, bool, int128, uint128, uint256, uint128, uint128, bytes calldata)
    external view override returns (bytes4)
{
    if (!allowAllSwappers[msg.sender] && !allowedSwapper[msg.sender][sender]) {
        revert IMetricOmmPoolActions.NotAllowedToSwap();
    }
    return IMetricOmmExtensions.beforeSwap.selector;
}
``` [3](#0-2) 

**Step 4 — `MetricOmmSimpleRouter` calls `pool.swap` directly, making itself `msg.sender`.**

```solidity
// metric-periphery/contracts/MetricOmmSimpleRouter.sol  line 104-112
(int128 amount0Delta, int128 amount1Delta) = IMetricOmmPoolActions(pool)
    .swap(
        i == last ? params.recipient : address(this),
        zeroForOne,
        amount,
        MetricOmmSwapPath.openLimit(zeroForOne),
        "",
        params.extensionDatas[i]
    );
``` [4](#0-3) 

When the router calls `pool.swap`, `msg.sender` inside the pool is the **router**, so `sender` delivered to the extension is the **router's address**, not the end user's address. The router does set callback context with the real user (`msg.sender` of the router call) for payment purposes, but that information is never surfaced to extensions. [5](#0-4) 

**The forced dilemma for pool admins:**

| Admin choice | Effect |
|---|---|
| Do **not** add router to allowlist | Allowlisted users cannot use the router at all |
| **Add router to allowlist** | Every user on the planet can bypass the per-user gate via the router |

There is no configuration that simultaneously allows router-mediated swaps for specific users while blocking others.

---

### Impact Explanation

A pool configured with `SwapAllowlistExtension` for KYC/compliance gating or market-maker-only access loses all enforcement the moment the router is added to the allowlist. Any non-allowlisted user can call `MetricOmmSimpleRouter.exactInputSingle` (or `exactInput` / `exactOutput`) targeting the restricted pool and execute swaps that the allowlist was designed to block. This is a direct curation failure on a live production pool, allowing unauthorized parties to trade against LP assets in a pool that was explicitly restricted.

---

### Likelihood Explanation

The likelihood is **high** for any pool that:
1. Deploys `SwapAllowlistExtension` to restrict swappers, **and**
2. Wants to support the standard periphery router for its allowlisted users.

Both conditions are the natural, expected use case for the extension. The admin has no way to discover the bypass from the extension's interface alone — `isAllowedToSwap(pool, router)` returning `true` looks correct from the admin's perspective.

---

### Recommendation

The extension must check the **actual end user**, not the intermediary. Two complementary fixes:

1. **In `MetricOmmPool.swap`**: accept an explicit `sender` parameter (similar to how `addLiquidity` accepts an explicit `owner`) so the router can forward the real user's address. The pool should validate that `msg.sender` is a trusted router before accepting a caller-supplied `sender`.

2. **Alternatively, in `MetricOmmSimpleRouter`**: pass the real user's address as the `sender` through `extensionData` and have `SwapAllowlistExtension` decode it, with the pool or extension verifying the router's identity before trusting the payload.

The simplest safe fix that requires no pool changes is to have the router encode the real user into `extensionData` and have the extension decode and check it when `msg.sender` (the pool's caller) is a known trusted router.

---

### Proof of Concept

```
Setup:
  pool configured with SwapAllowlistExtension
  alice  → setAllowedToSwap(pool, alice, true)
  router → setAllowedToSwap(pool, router, true)   ← admin adds router to support alice's router swaps

Attack:
  bob (not allowlisted) calls:
    router.exactInputSingle({pool: pool, ..., recipient: bob})

Trace:
  router.exactInputSingle()
    → pool.swap(recipient=bob, ...)          msg.sender = router
      → _beforeSwap(sender=router, ...)
        → SwapAllowlistExtension.beforeSwap(sender=router, ...)
          → allowedSwapper[pool][router] == true  ✓  (passes!)
      → swap executes, bob receives output tokens

Result:
  bob swaps successfully on a pool that explicitly disallowed him.
  The allowlist check passed because it saw the router, not bob.
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

**File:** metric-periphery/contracts/MetricOmmSimpleRouter.sol (L103-103)
```text
      _setNextCallbackContext(pool, CALLBACK_MODE_JUST_PAY, i == 0 ? msg.sender : address(this), params.tokens[i]);
```

**File:** metric-periphery/contracts/MetricOmmSimpleRouter.sol (L104-112)
```text
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
