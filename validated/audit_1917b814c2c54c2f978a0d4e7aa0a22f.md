### Title
`SwapAllowlistExtension` Checks Router Address Instead of End-User — Allowlist Fully Bypassed via `MetricOmmSimpleRouter` - (File: `metric-periphery/contracts/extensions/SwapAllowlistExtension.sol`)

---

### Summary

`SwapAllowlistExtension.beforeSwap` gates swaps by checking the `sender` argument passed by the pool. Because `MetricOmmPool.swap` sets `sender = msg.sender` (the immediate caller of the pool), any swap routed through `MetricOmmSimpleRouter` presents the **router's address** as `sender`, not the end-user's address. A pool admin who allowlists the router to enable router-based swaps for legitimate users simultaneously grants every non-allowlisted address the ability to bypass the per-user gate by routing through the same public router.

---

### Finding Description

**Call chain:**

```
User → MetricOmmSimpleRouter.exactInputSingle()
         → pool.swap(recipient, ..., extensionData)   [msg.sender = router]
              → _beforeSwap(msg.sender=router, ...)
                   → SwapAllowlistExtension.beforeSwap(sender=router, ...)
                        → allowedSwapper[pool][router]  ← checked, NOT the user
```

`MetricOmmPool.swap` unconditionally passes `msg.sender` as the `sender` argument to `_beforeSwap`:

```solidity
// MetricOmmPool.sol
_beforeSwap(
    msg.sender,   // ← router address when called via router
    recipient,
    ...
);
```

`SwapAllowlistExtension.beforeSwap` then evaluates:

```solidity
// SwapAllowlistExtension.sol
if (!allowAllSwappers[msg.sender] && !allowedSwapper[msg.sender][sender]) {
    revert IMetricOmmPoolActions.NotAllowedToSwap();
}
```

where `msg.sender` = pool and `sender` = router. The check resolves to `allowedSwapper[pool][router]`.

For the router path to work at all for any legitimate user, the pool admin must add the router to the allowlist. Once the router is allowlisted, the check `allowedSwapper[pool][router]` is `true` for every call that arrives through the router — regardless of who the actual end-user is. Any address, including one explicitly excluded from the allowlist, can call `exactInputSingle` / `exactInput` / `exactOutputSingle` / `exactOutput` on the router and the extension will pass.

The same structural problem applies to `simulateSwapAndRevert`, which also passes `msg.sender` as `sender` and invokes `_beforeSwap`.

---

### Impact Explanation

A pool configured with `SwapAllowlistExtension` to restrict trading to a curated set of addresses (e.g., KYC-verified counterparties, whitelisted market makers, or permissioned institutional participants) loses that restriction entirely for any user who routes through `MetricOmmSimpleRouter`. The allowlist guard silently passes for every router-mediated swap once the router is allowlisted, allowing non-permitted addresses to execute trades against pool liquidity. This constitutes a direct policy bypass on a curated pool and falls under the "Admin-boundary break: factory/oracle role checks are bypassed by an unprivileged path" and "Broken core pool functionality" impact categories.

---

### Likelihood Explanation

`MetricOmmSimpleRouter` is the primary public swap entrypoint for the protocol. Any pool that enables router-based swaps for legitimate users must allowlist the router. The bypass requires no special privileges, no flash loans, no multi-block setup, and no unusual token behavior — a single `exactInputSingle` call from any EOA suffices. Likelihood is high.

---

### Recommendation

The extension must gate on the **originating user**, not the immediate pool caller. Two sound approaches:

1. **Pass the original user through the router.** Add a `recipient`-style `swapper` parameter to the pool's swap interface that the router populates with `msg.sender` before calling the pool, and have the pool forward that value as `sender` to extensions. This requires a core interface change.

2. **Check `recipient` or a dedicated `swapper` field in `extensionData`.** The router can encode the real user address in `extensionData`; the extension decodes and verifies it. This is weaker (requires the router to be trusted to populate it honestly) but avoids a core change.

3. **Allowlist users, not routers.** Document that the router must never be added to the allowlist and that users must call the pool directly. This is operationally fragile and breaks the router UX for curated pools.

The cleanest fix is option 1: thread the originating user identity through the core hook arguments so extensions always see the economically relevant actor.

---

### Proof of Concept

```solidity
// SPDX-License-Identifier: MIT
pragma solidity ^0.8.35;

// Setup:
// 1. Deploy pool with SwapAllowlistExtension.
// 2. Pool admin allowlists `router` so legitimate users can swap via router.
//    extension.setAllowedToSwap(pool, address(router), true);
// 3. Pool admin does NOT allowlist `attacker`.
//    extension.isAllowedToSwap(pool, attacker) == false

// Attack:
// attacker calls router directly — extension sees sender=router, which IS allowlisted → passes
router.exactInputSingle(
    IMetricOmmSimpleRouter.ExactInputSingleParams({
        pool:             pool,
        recipient:        attacker,
        tokenIn:          token0,
        zeroForOne:       true,
        amountIn:         1e18,
        amountOutMinimum: 0,
        priceLimitX64:    0,
        deadline:         block.timestamp,
        extensionData:    ""
    })
);
// Swap executes successfully despite attacker not being on the allowlist.
// allowedSwapper[pool][attacker] == false, but allowedSwapper[pool][router] == true
// → extension check: allowedSwapper[pool][router] → true → no revert
```

**Root cause line:** [1](#0-0) 

**Pool passes `msg.sender` (router) as `sender`:** [2](#0-1) 

**Router calls pool with itself as `msg.sender`:** [3](#0-2) 

**Extension hook signature — `sender` is the only identity field checked:** [4](#0-3)

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
