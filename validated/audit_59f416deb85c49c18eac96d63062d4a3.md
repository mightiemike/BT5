### Title
SwapAllowlistExtension Gates the Router Address Instead of the Actual User, Allowing Any User to Bypass the Swap Allowlist via MetricOmmSimpleRouter — (`metric-periphery/contracts/extensions/SwapAllowlistExtension.sol`)

---

### Summary

`SwapAllowlistExtension.beforeSwap` checks the `sender` argument passed by the pool, which is `msg.sender` of the `pool.swap()` call. When a user routes through `MetricOmmSimpleRouter`, that `msg.sender` is the **router contract**, not the actual user. If the pool admin allowlists the router (the natural configuration for a pool that wants to support router-mediated swaps), every non-allowlisted user can bypass the curated-pool gate by routing through the router.

---

### Finding Description

**Call chain:**

```
User → MetricOmmSimpleRouter.exactInputSingle()
         → pool.swap(recipient, zeroForOne, amount, ..., extensionData)
              msg.sender = router
              → _beforeSwap(msg.sender=router, recipient, ...)
                   → SwapAllowlistExtension.beforeSwap(sender=router, ...)
                        checks allowedSwapper[pool][router]
```

In `MetricOmmPool.swap`, the pool passes `msg.sender` (the router) as the `sender` argument to `_beforeSwap`:

```solidity
// MetricOmmPool.sol line 230
_beforeSwap(
    msg.sender,   // ← router address when called via router
    recipient,
    ...
);
```

`ExtensionCalling._beforeSwap` forwards this verbatim to the extension:

```solidity
// ExtensionCalling.sol line 163
abi.encodeCall(IMetricOmmExtensions.beforeSwap, (sender, recipient, ...))
```

`SwapAllowlistExtension.beforeSwap` then checks:

```solidity
// SwapAllowlistExtension.sol line 37
if (!allowAllSwappers[msg.sender] && !allowedSwapper[msg.sender][sender]) {
    revert IMetricOmmPoolActions.NotAllowedToSwap();
}
```

Here `msg.sender` is the pool and `sender` is the **router**. The check is `allowedSwapper[pool][router]`, not `allowedSwapper[pool][actualUser]`.

**Bypass path:** A pool admin who wants to allow router-mediated swaps for allowlisted users must add the router to `allowedSwapper[pool][router] = true`. Once the router is allowlisted, **any** user — including those explicitly not on the allowlist — can call `router.exactInputSingle()` or `router.exactInput()` and the extension will pass because it only sees the router address.

---

### Impact Explanation

A curated pool using `SwapAllowlistExtension` to restrict trading to KYC'd, trusted, or otherwise vetted addresses is fully bypassed for any user who routes through `MetricOmmSimpleRouter`. Non-allowlisted adversarial actors can execute swaps against LP funds, extracting value through unfavorable oracle-anchored trades that the allowlist was intended to prevent. This is a direct loss of LP principal and a broken core pool invariant (the allowlist policy).

---

### Likelihood Explanation

The router is a first-party periphery contract. Any pool admin who wants allowlisted users to be able to use the router (the standard UX path) must allowlist the router address. This is the expected operational configuration, making the bypass reachable by any user on any such pool. No special privileges or malicious setup are required — only a standard `router.exactInputSingle()` call.

---

### Recommendation

The `SwapAllowlistExtension` must gate the **original user**, not the intermediate caller. Two options:

1. **Pass the original user through the router:** Have `MetricOmmSimpleRouter` forward `msg.sender` (the actual user) as a verified field in `extensionData`, and have the extension decode and check it. This requires a trust assumption that the router is the only allowed intermediary.

2. **Check `recipient` instead of `sender`:** If the pool's intent is to gate who receives value, check the `recipient` argument. However, this does not gate who initiates the trade.

3. **Preferred — check both `sender` and `recipient`:** Require that both the direct caller and the recipient are allowlisted, so neither the router nor an arbitrary recipient can be used as a bypass vector.

The cleanest fix is to document that the `SwapAllowlistExtension` is only safe for direct pool calls (not router-mediated), and provide a router-aware variant that decodes the original user from a signed or trusted `extensionData` field.

---

### Proof of Concept

```
1. Deploy pool with SwapAllowlistExtension configured.
2. Pool admin calls setAllowedToSwap(pool, router, true)
   — intending to allow router-mediated swaps for allowlisted users.
3. Pool admin does NOT call setAllowedToSwap(pool, alice, true)
   — alice is not allowlisted.
4. alice calls router.exactInputSingle({pool: pool, ...}).
5. Router calls pool.swap(...) with msg.sender = router.
6. Pool calls extension.beforeSwap(sender=router, ...).
7. Extension checks allowedSwapper[pool][router] == true → passes.
8. alice's swap executes on the curated pool despite not being allowlisted.
```

**Relevant code locations:**

- `sender` binding: [1](#0-0) 
- Extension dispatch: [2](#0-1) 
- Allowlist check (gates router, not user): [3](#0-2) 
- Router passes itself as `msg.sender` to pool: [4](#0-3)

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
