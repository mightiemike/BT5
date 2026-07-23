### Title
`SwapAllowlistExtension` Allowlist Bypassed via `MetricOmmSimpleRouter` — (File: `metric-periphery/contracts/extensions/SwapAllowlistExtension.sol`)

---

### Summary

`SwapAllowlistExtension.beforeSwap` gates swaps by checking the `sender` argument the pool passes to it. The pool always sets `sender = msg.sender`, i.e., the direct caller of `pool.swap()`. When a user routes through `MetricOmmSimpleRouter`, the pool's `msg.sender` is the **router**, not the actual user. If the router is allowlisted (which it must be for any legitimate user to use it), every non-allowlisted user can bypass the curated pool's swap gate simply by calling through the router.

---

### Finding Description

**Step 1 — Pool passes `msg.sender` as `sender` to the extension.**

In `MetricOmmPool.swap()`:

```solidity
// metric-core/contracts/MetricOmmPool.sol  line 230-240
_beforeSwap(
  msg.sender,   // ← whoever called pool.swap(); the router when routed
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

`ExtensionCalling._beforeSwap` forwards this verbatim as the first argument to every configured extension:

```solidity
// metric-core/contracts/ExtensionCalling.sol  line 160-176
_callExtensionsInOrder(
  BEFORE_SWAP_ORDER,
  abi.encodeCall(
    IMetricOmmExtensions.beforeSwap,
    (sender, recipient, zeroForOne, ...)
  )
);
```

**Step 2 — The allowlist extension checks that `sender` argument.**

```solidity
// metric-periphery/contracts/extensions/SwapAllowlistExtension.sol  line 37-39
if (!allowAllSwappers[msg.sender] && !allowedSwapper[msg.sender][sender]) {
  revert IMetricOmmPoolActions.NotAllowedToSwap();
}
```

`msg.sender` here is the pool; `sender` is whatever the pool forwarded — the router address when the user goes through `MetricOmmSimpleRouter`.

**Step 3 — The router calls `pool.swap()` directly, making itself the `msg.sender`.**

```solidity
// metric-periphery/contracts/MetricOmmSimpleRouter.sol  line 72-80
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

The actual user's address (`msg.sender` inside the router) is never forwarded to the pool or the extension.

**Resulting invariant break:**

| Who the admin intends to gate | Who the extension actually checks |
|---|---|
| The economic actor (the user) | The intermediary (the router) |

If the pool admin allowlists the router so that legitimate users can trade through it, every non-allowlisted address can also trade through the router. If the admin does not allowlist the router, every allowlisted user is blocked from using the router. Either way the allowlist is misapplied.

---

### Impact Explanation

A curated pool deploying `SwapAllowlistExtension` to restrict trading to KYC'd, whitelisted, or otherwise vetted addresses loses that restriction entirely for any user who calls `MetricOmmSimpleRouter.exactInputSingle`, `exactInput`, `exactOutputSingle`, or `exactOutput`. The attacker receives pool output tokens and the pool receives input tokens — a direct, fund-impacting bypass of the configured access control. This maps to **broken core pool functionality / admin-boundary break** under the allowed impact gate.

---

### Likelihood Explanation

`MetricOmmSimpleRouter` is the canonical, publicly deployed periphery contract. Any user can call it without permission. No special setup, privileged role, or non-standard token is required. The bypass is reachable on every swap on every allowlisted pool that also has the router allowlisted.

---

### Recommendation

The extension must gate the **economic actor**, not the intermediary. Two viable approaches:

1. **Require direct pool calls for allowlisted pools.** Document that pools using `SwapAllowlistExtension` must not allowlist the router, and users must call `pool.swap()` directly. This is operationally fragile.

2. **Pass the originating user through `extensionData`.** The router encodes `msg.sender` into `extensionData`; the extension decodes and checks it. This requires trusting the router not to forge the address, which is acceptable if the router is a known, immutable contract verified by the factory.

3. **Check `recipient` instead of `sender`.** If the pool's design intent is that the recipient is the gated party (the economic beneficiary), checking `recipient` is more robust. However, this changes the semantics of the allowlist.

The cleanest fix is approach 2 combined with a factory-verified router registry so the extension can confirm the `extensionData` origin is trustworthy.

---

### Proof of Concept

```
Setup:
  - Deploy pool with SwapAllowlistExtension configured in BEFORE_SWAP_ORDER
  - Pool admin calls setAllowedToSwap(pool, router, true)   // router must be allowed for legitimate use
  - Pool admin does NOT call setAllowedToSwap(pool, attacker, true)

Attack:
  - attacker calls MetricOmmSimpleRouter.exactInputSingle({
        pool: allowlistedPool,
        recipient: attacker,
        zeroForOne: true,
        amountIn: X,
        ...
    })

Trace:
  router.exactInputSingle()
    → pool.swap(recipient=attacker, ...) [msg.sender = router]
      → _beforeSwap(sender=router, ...)
        → SwapAllowlistExtension.beforeSwap(sender=router, ...)
          → allowedSwapper[pool][router] == true  ✓  (no revert)
      → swap executes, attacker receives output tokens

Result:
  attacker bypasses the allowlist and receives swap output.
  The allowlist check passed because the router is allowlisted,
  not because the attacker is allowlisted.
``` [1](#0-0) [2](#0-1) [3](#0-2) [4](#0-3)

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

**File:** metric-core/contracts/MetricOmmPool.sol (L230-241)
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
