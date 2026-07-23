### Title
`SwapAllowlistExtension` gates on immediate pool caller (`sender = router`) rather than originating user, allowing any unpermissioned address to bypass per-pool swap restrictions via `MetricOmmSimpleRouter` — (`File: metric-periphery/contracts/extensions/SwapAllowlistExtension.sol`)

---

### Summary

`SwapAllowlistExtension.beforeSwap` checks `allowedSwapper[pool][sender]` where `sender` is the direct caller of `pool.swap()`. When users route through `MetricOmmSimpleRouter`, `sender` becomes the router address, not the end user. A pool admin who allowlists the router (a natural UX decision so that allowlisted users can use the router) inadvertently opens the pool to **all** users, completely defeating the per-user access control the extension was designed to enforce.

---

### Finding Description

`MetricOmmPool.swap()` passes `msg.sender` as the `sender` argument to `_beforeSwap`: [1](#0-0) 

`ExtensionCalling._beforeSwap` forwards that value unchanged to the configured extension: [2](#0-1) 

`SwapAllowlistExtension.beforeSwap` then checks the allowlist keyed on `(pool, sender)`: [3](#0-2) 

When a user calls `MetricOmmSimpleRouter.exactInputSingle()`, the router calls `pool.swap(params.recipient, ...)` directly: [4](#0-3) 

Inside the pool, `msg.sender` is the **router address**, so `sender = router` is what the extension receives. The extension checks `allowedSwapper[pool][router]`, not `allowedSwapper[pool][actualUser]`.

This creates an irreconcilable dilemma for pool admins:

| Admin choice | Consequence |
|---|---|
| Do **not** allowlist the router | Allowlisted users cannot use the router at all |
| **Allowlist the router** (for UX) | Every user on-chain can bypass the allowlist by routing through the router |

The second path is the natural UX choice (the protocol's own `generate_scanned_questions.py` flags this exact tension), and it silently nullifies the allowlist for the entire pool. [5](#0-4) 

---

### Impact Explanation

Any address — including addresses the pool admin explicitly excluded — can execute swaps against a `SwapAllowlistExtension`-protected pool by calling `MetricOmmSimpleRouter.exactInputSingle` or `exactInput`. The allowlist guard is completely bypassed: the extension approves the swap because it sees the allowlisted router, not the non-allowlisted originator. This breaks the core access-control invariant the extension is designed to enforce and constitutes an admin-boundary break reachable by an unprivileged path.

---

### Likelihood Explanation

The router is the primary UX entry point for swaps. Any pool admin who wants allowlisted users to have a normal swap UX must allowlist the router. Once the router is allowlisted, the bypass is available to every address on-chain with zero additional preconditions. No special role, no privileged setup, and no malicious pool configuration is required — a standard `exactInputSingle` call suffices.

---

### Recommendation

The extension must gate on the **originating user**, not the immediate pool caller. Two viable approaches:

1. **Check `recipient` instead of `sender`**: For single-hop swaps the recipient is the economic beneficiary. The extension signature already receives `recipient` as the second argument; gate on `allowedSwapper[pool][recipient]` instead of `sender`.

2. **Pass originator through `extensionData`**: Require the router to embed `msg.sender` in `extensionData` and have the extension decode and verify it. This requires a coordinated change to the router and extension.

Option 1 is simpler but breaks for multi-hop paths where intermediate recipients are the router itself. Option 2 is more robust but requires the extension to trust the router's encoding.

---

### Proof of Concept

```
Setup:
  pool configured with SwapAllowlistExtension
  allowedSwapper[pool][alice] = true        // alice is KYC'd
  allowedSwapper[pool][router] = true       // admin allowlists router so alice can use it
  allowedSwapper[pool][attacker] = false    // attacker is NOT allowlisted

Attack:
  attacker calls MetricOmmSimpleRouter.exactInputSingle({
      pool: pool,
      recipient: attacker,
      ...
  })

  Router calls pool.swap(recipient=attacker, ...)
  Pool: msg.sender = router → sender = router
  Extension checks: allowedSwapper[pool][router] == true → PASSES
  Attacker successfully swaps in a pool they were explicitly excluded from.

Result:
  SwapAllowlistExtension guard is fully bypassed.
  Any address can swap by routing through MetricOmmSimpleRouter.
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

**File:** generate_scanned_questions.py (L655-663)
```python
        Target(
            short="swap allowlist gate",
            file_function="metric-periphery/contracts/extensions/SwapAllowlistExtension.sol::beforeSwap",
            entrypoint="metric-core/contracts/MetricOmmPool.sol::swap and metric-periphery/contracts/MetricOmmSimpleRouter.sol::exact*",
            call_path="public swap -> beforeSwap hook -> allowAll/allowedSwapper lookup keyed by pool and sender",
            values="the exact swapper identity checked by the hook and whether router-mediated swaps preserve that identity",
            control_hint="Because public users may enter through the router, the hook must gate the same actor the pool designers thought they were allowlisting.",
            validation_focus="Test direct swaps and router swaps on allowlisted pools and assert the hook cannot be bypassed by routing through an intermediate public contract.",
        ),
```
