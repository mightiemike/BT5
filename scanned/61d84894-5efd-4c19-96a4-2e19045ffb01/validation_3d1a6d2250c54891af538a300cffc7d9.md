### Title
SwapAllowlistExtension gates the router's address instead of the end-user, allowing any caller to bypass a curated pool's swap allowlist via MetricOmmSimpleRouter — (`metric-periphery/contracts/extensions/SwapAllowlistExtension.sol`)

---

### Summary

`SwapAllowlistExtension.beforeSwap` checks the `sender` argument passed by the pool, which is always `msg.sender` of the pool's `swap()` call. When a user routes through `MetricOmmSimpleRouter`, that `msg.sender` is the router contract, not the end-user. If the pool admin allowlists the router to support the standard periphery flow, every unpermissioned user can bypass the per-user allowlist by routing through the router.

---

### Finding Description

`MetricOmmPool.swap()` calls `_beforeSwap(msg.sender, recipient, ...)`: [1](#0-0) 

`ExtensionCalling._beforeSwap` forwards that `sender` (= `msg.sender` of the pool call) verbatim to every configured extension: [2](#0-1) 

`SwapAllowlistExtension.beforeSwap` then checks `allowedSwapper[msg.sender][sender]`, where `msg.sender` is the pool (used as the mapping key) and `sender` is the value forwarded above: [3](#0-2) 

When a user calls `MetricOmmSimpleRouter.exactInputSingle()`, the router calls `pool.swap(params.recipient, ...)` directly: [4](#0-3) 

The pool's `msg.sender` is the router, so the extension evaluates `allowedSwapper[pool][router]` — not `allowedSwapper[pool][user]`. The actual end-user's identity is never checked. The same applies to `exactInput`, `exactOutputSingle`, and `exactOutput`. [5](#0-4) 

---

### Impact Explanation

Two outcomes, both fund-impacting:

**Path A — Admin allowlists the router (expected production setup):** The pool admin adds the router to the allowlist so that allowlisted users can use the standard periphery. Because the extension checks the router's address, every unpermissioned user can now swap on the curated pool by routing through `MetricOmmSimpleRouter`. The per-user allowlist is completely nullified. Any user can drain LP assets at oracle-derived prices that the pool admin intended to restrict to vetted counterparties.

**Path B — Admin does not allowlist the router:** Router-mediated swaps revert for everyone, including allowlisted users. The standard periphery path is broken for the pool, forcing allowlisted users to call the pool directly and preventing them from using multi-hop routing or permit-based flows.

Both paths represent broken core pool functionality or direct unauthorized access to LP assets, satisfying the Metric OMM allowed impact gate (allowlist bypass → unauthorized swap → direct loss of LP principal or owed fees).

---

### Likelihood Explanation

The `MetricOmmSimpleRouter` is the documented, production-supported entry point for swaps. Any user who wants to bypass a curated pool's allowlist needs only to call `exactInputSingle` on the router with the target pool. No privileged access, no special setup, and no malicious token is required. The attacker controls only the timing and the pool address. Likelihood is high.

---

### Recommendation

The `SwapAllowlistExtension` must gate the economically relevant actor — the end-user — not the intermediary. Two complementary fixes:

1. **Pass the original caller through the router.** Add an `originator` field to the swap call or use a trusted-forwarder pattern so the pool can supply the real user's address as `sender` when the call originates from a known periphery contract.

2. **Gate on `recipient` as a fallback.** For pools that use the allowlist to restrict who receives output tokens, check `recipient` instead of (or in addition to) `sender`. This is already available in the `beforeSwap` signature.

3. **Document the invariant explicitly.** Until a forwarding mechanism exists, the `SwapAllowlistExtension` NatSpec should state that it only enforces the allowlist for direct pool calls, and pool admins must not allowlist the router if they intend per-user gating.

---

### Proof of Concept

```
Setup:
  - Deploy pool with SwapAllowlistExtension configured as beforeSwap hook.
  - Pool admin calls setAllowedToSwap(pool, router, true)
    (to allow allowlisted users to use the standard periphery).
  - Pool admin does NOT call setAllowedToSwap(pool, attacker, true).

Attack:
  1. attacker calls MetricOmmSimpleRouter.exactInputSingle({
       pool: curated_pool,
       recipient: attacker,
       ...
     })
  2. Router calls pool.swap(attacker, ...) — msg.sender to pool = router.
  3. Pool calls _beforeSwap(router, attacker, ...).
  4. SwapAllowlistExtension checks allowedSwapper[pool][router] == true → passes.
  5. Swap executes; attacker receives output tokens from the curated pool.

Result:
  attacker, who was never individually allowlisted, successfully swaps on the
  curated pool. The per-user allowlist invariant is broken.
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

**File:** metric-periphery/contracts/MetricOmmSimpleRouter.sol (L99-112)
```text
    for (uint256 i = 0; i <= last; i++) {
      address pool = params.pools[i];
      bool zeroForOne = MetricOmmSwapPath.resolveZeroForOneBitmap(params.zeroForOneBitMap, i);

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
