### Title
`SwapAllowlistExtension.beforeSwap` checks the router address instead of the actual user, allowing any user to bypass the swap allowlist via `MetricOmmSimpleRouter` — (`metric-periphery/contracts/extensions/SwapAllowlistExtension.sol`)

---

### Summary

`SwapAllowlistExtension.beforeSwap` gates swaps by checking the `sender` argument, which is `msg.sender` of the pool's `swap()` call. When a swap is routed through `MetricOmmSimpleRouter`, `sender` is the **router address**, not the actual user. If the pool admin allowlists the router (the natural step to enable router-mediated swaps for allowlisted users), any non-allowlisted user can bypass the allowlist entirely by routing through the router.

---

### Finding Description

**Call chain for a router-mediated swap:**

```
user → MetricOmmSimpleRouter.exactInputSingle()
     → pool.swap(recipient, ..., extensionData)   // msg.sender = router
     → ExtensionCalling._beforeSwap(sender=router, ...)
     → SwapAllowlistExtension.beforeSwap(sender=router, ...)
```

`MetricOmmSimpleRouter.exactInputSingle` calls `pool.swap()` directly: [1](#0-0) 

The pool then dispatches `_beforeSwap` with `msg.sender` (the router) as `sender`: [2](#0-1) 

`ExtensionCalling._beforeSwap` encodes `sender` (= router) into the extension call: [3](#0-2) 

`SwapAllowlistExtension.beforeSwap` then checks `allowedSwapper[pool][sender]` — i.e., `allowedSwapper[pool][router]`: [4](#0-3) 

**The dilemma this creates for the pool admin:**

| Admin choice | Effect |
|---|---|
| Do **not** allowlist the router | Allowlisted users cannot use the router at all — broken UX |
| **Allowlist the router** | Any non-allowlisted user can bypass the allowlist via the router — broken security |

There is no correct configuration. The extension has no mechanism to recover the actual user's identity from `extensionData`; it ignores that field entirely.

---

### Impact Explanation

A curated pool deploying `SwapAllowlistExtension` to restrict trading to a set of trusted counterparties (e.g., KYC'd institutions, whitelisted market makers, or partners) loses that protection entirely for router-mediated swaps. Any non-allowlisted user can call `MetricOmmSimpleRouter.exactInputSingle` or `exactInput` targeting the pool and the guard passes because the router is allowlisted. Unauthorized traders with adverse information or MEV intent can drain LP value from a pool that was explicitly designed to exclude them. This breaks the core pool invariant stated in the README: *"A curated pool must enforce the same allowlist policy regardless of which supported public entrypoint reaches it."* [5](#0-4) 

---

### Likelihood Explanation

The router is the primary user-facing swap interface. Any pool admin who wants allowlisted users to be able to use the router (the normal expectation) must allowlist the router address, which immediately opens the bypass to all users. The trigger requires no privileged action beyond the pool admin's own reasonable configuration step. The attacker needs only to call the public router.

---

### Recommendation

The extension must check the **actual user's identity**, not the intermediary router's address. Two viable approaches:

1. **Pass the real user through `extensionData`:** The router encodes `msg.sender` into `extensionData`; the extension decodes and verifies it. This requires a trusted encoding convention between the router and the extension.
2. **Check `sender` only when `sender` is not a known router:** The extension maintains a registry of trusted routers and, when `sender` is a router, falls back to checking a user identity embedded in `extensionData`.

The simplest safe fix is to have the router always encode the originating user in `extensionData` and have `SwapAllowlistExtension.beforeSwap` decode and check that address when `sender` is a recognized router.

---

### Proof of Concept

1. Deploy a pool with `SwapAllowlistExtension` configured.
2. Pool admin calls `setAllowedToSwap(pool, alice, true)` — Alice is the only allowed trader.
3. Pool admin calls `setAllowedToSwap(pool, router, true)` — router is allowlisted so Alice can use it.
4. `bob` (not allowlisted) calls `MetricOmmSimpleRouter.exactInputSingle({pool: pool, ...})`.
5. Router calls `pool.swap(...)` with `msg.sender = router`.
6. Extension checks `allowedSwapper[pool][router]` → `true` → swap succeeds.
7. Bob trades on a pool he was explicitly excluded from.

Direct pool call by Bob (`pool.swap(...)` directly) would correctly revert with `NotAllowedToSwap` because `allowedSwapper[pool][bob]` is `false`. The router path silently bypasses this check. [6](#0-5) [7](#0-6)

### Citations

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

**File:** metric-core/contracts/ExtensionCalling.sol (L149-170)
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

**File:** README.md (L44-51)
```markdown
### Q: What properties/invariants do you want to hold even if breaking them has a low/unknown impact?
Solvency: pool token balances always cover all LP claims + owed fees; every LP can withdraw their proportional share. Withdraw (remove-liquidity) must work even when the pool is paused (pause only blocks swaps).
Swap conservation: exact settlement — the pool receives the owed input (else IncorrectDelta revert) and never creates/leaks value; a trader never receives more than the bin curve allows.
Quote sanity: bid > 0 and bid < ask always (hard invariant; BidIsZero / BidGreaterThanAsk).
Anchored band: every AnchoredPriceProvider quote — including source mode — stays within mid ± (u + floor); an unreviewed source can never push price outside the band.
No trade on bad oracle: swaps revert on stale price (maxTimeDelta/maxRefStaleness), excessive Chainlink deviation, or (L2) sequencer down.

Issues related to Invariant violations can be considered valid if they lead to Medium or higher impact and qualify for Medium or higher severity definition.
```
