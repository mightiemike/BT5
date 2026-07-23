### Title
`SwapAllowlistExtension` Checks Router Address Instead of Original User, Allowing Full Allowlist Bypass — (`metric-periphery/contracts/extensions/SwapAllowlistExtension.sol`)

---

### Summary

`SwapAllowlistExtension.beforeSwap` gates swaps by checking the `sender` argument passed from the pool. When a user routes through `MetricOmmSimpleRouter`, the pool's `msg.sender` is the router contract, so the extension checks `allowedSwapper[pool][router]` instead of `allowedSwapper[pool][user]`. If the router is allowlisted (a natural admin action to permit router-based swaps), every unprivileged user bypasses the allowlist entirely.

---

### Finding Description

`MetricOmmPool.swap` captures `msg.sender` at entry and forwards it as the `sender` argument through `ExtensionCalling._beforeSwap`: [1](#0-0) 

`ExtensionCalling._beforeSwap` encodes that value verbatim into the hook call: [2](#0-1) 

`SwapAllowlistExtension.beforeSwap` then checks `allowedSwapper[msg.sender][sender]`, where `msg.sender` is the pool and `sender` is whoever called `pool.swap()`: [3](#0-2) 

`MetricOmmSimpleRouter.exactInputSingle` (and every other router entry point) calls `pool.swap()` directly, making the router the `msg.sender` inside the pool for every hop: [4](#0-3) 

The same pattern holds for `exactInput` (all hops), `exactOutputSingle`, and `exactOutput`: [5](#0-4) 

The result: the extension never sees the original user's address — it sees the router's address. The `sender` identity is "fixed" to the intermediate contract at the moment the pool is entered, exactly analogous to the external report's initialization-time parameter freeze.

---

### Impact Explanation

Two fund-impacting failure modes arise:

1. **Allowlist bypass (primary impact):** A pool admin allowlists the router so that permitted users can swap through it. Because the extension checks `allowedSwapper[pool][router]`, every user — including those the admin explicitly excluded — can call `MetricOmmSimpleRouter` and pass the guard. The allowlist is completely neutralised; restricted pools become open to all swappers. Any value-extraction or regulatory restriction the allowlist was meant to enforce is lost.

2. **Broken functionality for legitimate users:** If the admin does *not* allowlist the router (to avoid the bypass above), then every allowlisted user who calls the router is rejected with `NotAllowedToSwap`. Multi-hop swaps and exact-output paths are unavailable to the very users the pool was designed to serve, breaking core swap functionality.

---

### Likelihood Explanation

- The `MetricOmmSimpleRouter` is the canonical public swap entry point; ordinary users are expected to call it.
- No special privilege, flash loan, or unusual token behaviour is required — any EOA can call `exactInputSingle`.
- The bypass is triggered on every router-mediated swap to an allowlisted pool, making it continuously reachable.

---

### Recommendation

Pass the original user's address through the hook rather than the immediate pool caller. Two complementary approaches:

1. **Preferred — thread the original sender:** Add an `originalSender` field to `extensionData` that the router populates with `msg.sender` before calling the pool. Extensions that need the true user identity decode it from `extensionData`. This is the cleanest separation because it does not change the core hook signature.

2. **Alternative — check `sender` against a router registry:** In `SwapAllowlistExtension.beforeSwap`, if `sender` is a known router, decode the real user from `extensionData` and check that address instead.

Either way, the extension must never treat the router's address as the identity to gate.

---

### Proof of Concept

```
Setup:
  pool P configured with SwapAllowlistExtension E
  admin calls E.setAllowedToSwap(P, router, true)   // router allowlisted so users can swap
  admin calls E.setAllowedToSwap(P, alice, false)    // alice is NOT allowlisted

Attack:
  alice calls MetricOmmSimpleRouter.exactInputSingle({pool: P, ...})
    → router calls P.swap(recipient, ...)
    → pool captures msg.sender = router
    → _beforeSwap(sender=router, ...)
    → E.beforeSwap(sender=router, ...)
    → allowedSwapper[P][router] == true  ✓  (no revert)
    → swap executes; alice receives output tokens

Result:
  alice, an explicitly excluded address, completes a swap in a restricted pool.
  The allowlist invariant is broken; any user can replicate this.
``` [6](#0-5) [4](#0-3)

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

**File:** metric-periphery/contracts/MetricOmmSimpleRouter.sol (L103-112)
```text
      _setNextCallbackContext(pool, CALLBACK_MODE_JUST_PAY, i == 0 ? msg.sender : address(this), params.tokens[i]);
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
