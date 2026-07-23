### Title
Router-Mediated Swaps Pass Router Address as `sender` to `SwapAllowlistExtension`, Allowing Any User to Bypass Per-User Allowlist — (`metric-periphery/contracts/extensions/SwapAllowlistExtension.sol`)

---

### Summary

`SwapAllowlistExtension.beforeSwap` gates swaps by checking `allowedSwapper[pool][sender]`, where `sender` is the immediate caller of `MetricOmmPool.swap()`. When a user routes through `MetricOmmSimpleRouter`, the pool's `msg.sender` is the **router address**, not the original user. If the pool admin allowlists the router to support router-mediated swaps, every user — including non-allowlisted ones — can bypass the per-user restriction by routing through the public router.

---

### Finding Description

**Call chain:**

```
User → MetricOmmSimpleRouter.exactInputSingle()
         → IMetricOmmPoolActions(pool).swap(recipient, ...)   // msg.sender = router
              → _beforeSwap(msg.sender=router, ...)
                   → SwapAllowlistExtension.beforeSwap(sender=router, ...)
                        → allowedSwapper[pool][router]  ← checks router, not user
```

**Step 1 — Pool passes `msg.sender` as `sender` to the hook:** [1](#0-0) 

`msg.sender` here is whoever called `pool.swap()`. When the router calls it, `msg.sender` is the router.

**Step 2 — `ExtensionCalling._beforeSwap` forwards that `sender` verbatim to the extension:** [2](#0-1) 

**Step 3 — `SwapAllowlistExtension.beforeSwap` checks `allowedSwapper[pool][sender]`:** [3](#0-2) 

`msg.sender` = pool, `sender` = router. The check is `allowedSwapper[pool][router]`, not `allowedSwapper[pool][originalUser]`.

**Step 4 — The router never forwards the original user to `pool.swap()`:** [4](#0-3) 

The original `msg.sender` is stored only in transient callback context for payment, never passed as the swap `sender`.

**The dilemma this creates:**

| Pool admin configuration | Direct swap (allowlisted user) | Router swap (allowlisted user) | Router swap (non-allowlisted user) |
|---|---|---|---|
| Allowlist users only, NOT router | ✓ passes | ✗ fails | ✗ fails |
| Allowlist router (to enable router swaps) | ✓ passes | ✓ passes | **✓ passes — bypass!** |

There is no configuration that simultaneously enables router-mediated swaps for allowlisted users and blocks non-allowlisted users. Allowlisting the router grants access to **all** users.

---

### Impact Explanation

A pool admin who deploys a curated pool with `SwapAllowlistExtension` to restrict trading to specific counterparties (e.g., KYC'd addresses, institutional partners) cannot enforce that restriction when the public router is used. Any unprivileged user can bypass the allowlist by calling `exactInputSingle`, `exactInput`, `exactOutputSingle`, or `exactOutput` on `MetricOmmSimpleRouter`. This breaks the core curation guarantee of the extension and constitutes broken core pool functionality — the allowlist is rendered ineffective for the supported public entrypoint.

---

### Likelihood Explanation

The `MetricOmmSimpleRouter` is the primary supported public entrypoint for swaps. Any user aware of the router can exploit this without any special privileges, tokens, or setup. The likelihood is high whenever a pool uses `SwapAllowlistExtension` and the router is allowlisted.

---

### Recommendation

The `sender` passed to `beforeSwap` must represent the **originating user**, not the intermediate router. Two options:

1. **Pass original user through `extensionData`**: The router encodes `msg.sender` into `extensionData`, and the extension reads it. This requires a trust assumption that the router is the caller (verifiable via `allowedSwapper[pool][msg.sender_of_extension]` being the router).

2. **Preferred — check `msg.sender` of the extension call against a router registry, then read originating user from `extensionData`**: The extension verifies the pool called it, then reads the true originating user from a signed or router-provided field in `extensionData`.

3. **Simplest — document that the allowlist gates the immediate caller**: If the design intent is to gate the immediate caller (router or EOA), document it clearly and require pool admins to understand that allowlisting the router opens access to all users.

---

### Proof of Concept

```solidity
// Pool configured with SwapAllowlistExtension
// Pool admin allowlists only `alice` and the router (to support router swaps):
//   allowedSwapper[pool][alice] = true
//   allowedSwapper[pool][router] = true

// Mallory (not allowlisted) calls:
router.exactInputSingle(ExactInputSingleParams({
    pool: curated_pool,
    recipient: mallory,
    zeroForOne: true,
    amountIn: 1e18,
    amountOutMinimum: 0,
    priceLimitX64: 0,
    deadline: block.timestamp,
    extensionData: ""
}));
// pool.swap() is called with msg.sender = router
// beforeSwap receives sender = router
// allowedSwapper[pool][router] = true → check passes
// Mallory's swap succeeds despite not being allowlisted
``` [5](#0-4) [6](#0-5)

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
