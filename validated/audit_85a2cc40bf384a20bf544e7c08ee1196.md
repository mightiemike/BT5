The call path is fully traceable and the vulnerability is real. Here is the complete analysis:

---

### Title
Wrong Actor Checked in `beforeSwap` Allowlist: Router Address Replaces Original User — (`metric-periphery/contracts/extensions/SwapAllowlistExtension.sol`)

---

### Summary

`SwapAllowlistExtension.beforeSwap` gates swaps by checking `allowedSwapper[pool][sender]`, where `sender` is `msg.sender` of the pool's `swap()` call. When a user routes through `MetricOmmSimpleRouter`, the pool's `msg.sender` is the **router contract**, not the original user. If the pool admin allowlists the router to enable router-mediated swaps, the allowlist is completely bypassed: any unprivileged user can call the router and swap on a curated pool.

---

### Finding Description

**Full call trace — direct swap (correct):**

```
user → MetricOmmPool.swap()
  msg.sender = user
  _beforeSwap(sender=user, ...)                          [MetricOmmPool.sol:230]
  → ExtensionCalling._beforeSwap(sender=user, ...)       [ExtensionCalling.sol:160]
  → SwapAllowlistExtension.beforeSwap(sender=user, ...)  [SwapAllowlistExtension.sol:37]
  check: allowedSwapper[pool][user]  ✓ correct actor
```

**Full call trace — router-mediated swap (broken):**

```
user → MetricOmmSimpleRouter.exactInputSingle()
  router → MetricOmmPool.swap()
    msg.sender = router (not user)
    _beforeSwap(sender=router, ...)                          [MetricOmmPool.sol:230]
    → ExtensionCalling._beforeSwap(sender=router, ...)       [ExtensionCalling.sol:160]
    → SwapAllowlistExtension.beforeSwap(sender=router, ...)  [SwapAllowlistExtension.sol:37]
    check: allowedSwapper[pool][router]  ✗ wrong actor
```

The pool passes `msg.sender` as `sender` to `_beforeSwap`: [1](#0-0) 

`ExtensionCalling._beforeSwap` forwards that `sender` verbatim to the extension: [2](#0-1) 

The extension checks `allowedSwapper[msg.sender][sender]` where `msg.sender` is the pool and `sender` is whoever called `pool.swap()` — the router, not the original user: [3](#0-2) 

The router calls `pool.swap()` directly, making itself the `msg.sender` of the pool: [4](#0-3) 

---

### Impact Explanation

**Bypass scenario (primary):** A pool admin configures an allowlist for a curated pool (e.g., KYC-gated, institutional). To allow their allowlisted users to also use the router, the admin adds `allowedSwapper[pool][router] = true`. At that point, **any** unprivileged user can call `MetricOmmSimpleRouter.exactInputSingle/exactInput/exactOutputSingle/exactOutput` and swap on the curated pool, completely defeating the allowlist. The attacker pays no special cost and needs no privileged access.

**Lockout scenario (secondary):** If the admin does not allowlist the router, their own allowlisted users cannot use the router at all — the hook rejects them because it sees the router address. This breaks the expected usability of the periphery for curated pools.

Both outcomes are direct consequences of the hook keying authorization to the wrong actor.

---

### Likelihood Explanation

- The router is the standard, documented periphery entrypoint. Pool admins are expected to support it.
- Allowlisting the router is the only way to let allowlisted users use the router, making the bypass a natural consequence of normal admin configuration.
- No special timing, oracle manipulation, or state precondition is required. Any user can exploit this in a single transaction.
- The "alternating swap directions / rolling observations / guard threshold" framing in the question is irrelevant noise — the bug is purely in actor identity, present on every router-mediated swap.

---

### Recommendation

Pass the **original user** through the call stack rather than the immediate `msg.sender`. Two options:

1. **Preferred:** Add an `originator` field to the swap callback data or use transient storage (similar to how the router already stores `_getPayer()` in transient storage) so the pool can forward the true initiator to extensions.
2. **Alternative:** Document that the `SwapAllowlistExtension` is incompatible with router-mediated swaps and enforce this at the factory level (e.g., reject pool configurations that combine the allowlist extension with a public router allowlist entry).

---

### Proof of Concept

```solidity
// Setup: pool admin creates a curated pool with SwapAllowlistExtension
// Admin allowlists the router so their users can use it:
swapAllowlist.setAllowedToSwap(pool, address(router), true);

// Attacker (not in allowlist) calls the router directly:
router.exactInputSingle(ExactInputSingleParams({
    pool: curatedPool,
    recipient: attacker,
    zeroForOne: true,
    amountIn: 1e18,
    amountOutMinimum: 0,
    priceLimitX64: 0,
    deadline: block.timestamp,
    extensionData: ""
}));
// → pool.swap() is called with msg.sender = router
// → beforeSwap checks allowedSwapper[pool][router] = true  ✓
// → swap executes for the attacker despite not being allowlisted
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

**File:** metric-core/contracts/ExtensionCalling.sol (L159-177)
```text
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
