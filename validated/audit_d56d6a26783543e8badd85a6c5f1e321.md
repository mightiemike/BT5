### Title
Router-Mediated Swap Bypasses SwapAllowlistExtension Because `sender` Is the Router, Not the End User — (`metric-periphery/contracts/extensions/SwapAllowlistExtension.sol`)

---

### Summary

`SwapAllowlistExtension.beforeSwap` gates swaps by checking `allowedSwapper[pool][sender]`, where `sender` is the `msg.sender` of `MetricOmmPool.swap()`. When a user routes through `MetricOmmSimpleRouter`, the pool's `msg.sender` is the **router contract**, not the end user. If the pool admin allowlists the router (a natural step to enable router-mediated swaps for their allowlisted users), every user — including non-allowlisted ones — can bypass the gate by routing through the router.

---

### Finding Description

**Step 1 — Pool calls `_beforeSwap` with `msg.sender` as `sender`:**

In `MetricOmmPool.swap()`, the `sender` forwarded to the extension is `msg.sender` of the pool call: [1](#0-0) 

`ExtensionCalling._beforeSwap` encodes that value verbatim as the first argument to the extension: [2](#0-1) 

**Step 2 — Extension checks `allowedSwapper[pool][sender]`:** [3](#0-2) 

`msg.sender` here is the pool; `sender` is whoever called `pool.swap()`.

**Step 3 — Router calls `pool.swap()` as itself:**

`exactInputSingle` (and all other `exact*` entry points) calls `pool.swap()` directly: [4](#0-3) 

The pool's `msg.sender` is the **router address**. The end user's address is stored only in transient callback context (`_getPayer()`), never forwarded to the pool or the extension.

**Step 4 — The bypass:**

| Configuration | Direct swap by non-allowlisted user | Router swap by non-allowlisted user |
|---|---|---|
| Router NOT allowlisted | Blocked ✓ | Blocked ✓ (but allowlisted users also can't use router) |
| Router IS allowlisted | Blocked ✓ | **Passes — bypass** ✗ |

A pool admin who wants their allowlisted users to be able to use the router must allowlist the router address. Once they do, `allowedSwapper[pool][router] == true`, and the check passes for **any** caller of the router, regardless of whether that caller is on the allowlist.

The extension has no way to recover the true end user: the router stores the payer in transient storage for its own callback, but that information is never passed to `beforeSwap`.

---

### Impact Explanation

Any user can bypass a `SwapAllowlistExtension`-gated pool by routing through `MetricOmmSimpleRouter` whenever the router is allowlisted. This completely defeats the purpose of the allowlist for pools that need to restrict access (e.g., institutional-only pools, KYC-gated pools, or pools with favorable pricing for specific counterparties). Non-allowlisted users gain full swap access to a pool that was designed to exclude them.

---

### Likelihood Explanation

The router is the standard periphery entry point. Pool admins who want their allowlisted users to be able to use the router — a completely reasonable and expected desire — will allowlist the router. The bypass is then available to any public user with no special privileges, no malicious setup, and no non-standard token behavior required.

---

### Recommendation

The extension must verify the **economic actor** (the entity providing tokens), not the immediate caller. Two options:

1. **Pass the payer through `extensionData`**: The router encodes the true payer in `extensionData`; the extension reads and verifies it. This requires a convention between router and extension.
2. **Check `recipient` instead of `sender` for router flows, or require direct pool calls only**: Document that the allowlist only works for direct pool calls and that the router must not be allowlisted.
3. **Preferred — add a `trustedForwarder` concept**: The extension maps trusted intermediaries (e.g., the router) to a "read payer from extensionData" mode, and verifies the payer address encoded there against the allowlist.

---

### Proof of Concept

```
Setup:
  pool configured with SwapAllowlistExtension
  allowedSwapper[pool][alice] = true       // alice is KYC'd
  allowedSwapper[pool][router] = true      // admin allowlists router so alice can use it
  allowedSwapper[pool][attacker] = false   // attacker is NOT allowlisted

Attack:
  attacker calls router.exactInputSingle({pool: pool, recipient: attacker, ...})
  → router calls pool.swap(attacker, ...)
  → pool calls _beforeSwap(msg.sender=router, ...)
  → extension checks allowedSwapper[pool][router] == true  ✓
  → swap executes for attacker despite attacker not being allowlisted

Assert:
  attacker successfully swaps on a pool they should be excluded from.
  The allowlist is completely bypassed.
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
