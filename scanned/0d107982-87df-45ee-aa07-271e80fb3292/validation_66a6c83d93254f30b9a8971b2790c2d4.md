### Title
`SwapAllowlistExtension.beforeSwap` gates the router address instead of the real swapper, enabling full allowlist bypass via `MetricOmmSimpleRouter` — (`metric-periphery/contracts/extensions/SwapAllowlistExtension.sol`)

---

### Summary

`SwapAllowlistExtension` is supposed to restrict swaps on a curated pool to a set of approved addresses. However, it checks the `sender` argument passed by the pool, which is the pool's own `msg.sender`. When a user enters through `MetricOmmSimpleRouter`, the pool's `msg.sender` is the router contract, not the original user. The extension therefore checks whether the **router** is allowlisted, not whether the **user** is allowlisted. This creates an inescapable dilemma for pool admins: either allowlisted users cannot use the router at all, or the admin allowlists the router and every unprivileged user can bypass the allowlist.

---

### Finding Description

**Pool's `swap` passes `msg.sender` as `sender`:** [1](#0-0) 

The pool calls `_beforeSwap(msg.sender, recipient, ...)`. When the user enters through `MetricOmmSimpleRouter.exactInputSingle` or `exactInput`, the pool's `msg.sender` is the router contract.

**Router calls the pool directly, substituting itself as `msg.sender`:** [2](#0-1) 

The router calls `pool.swap(params.recipient, ...)` with no mechanism to forward the original caller's identity.

**Extension checks the wrong actor:** [3](#0-2) 

`allowedSwapper[msg.sender][sender]` resolves to `allowedSwapper[pool][router]`. The real user's address is never consulted.

---

### Impact Explanation

A pool admin who deploys a curated pool with `SwapAllowlistExtension` and allowlists specific counterparties (e.g., Alice and Bob) faces the following:

- **Path A – router not allowlisted**: Alice and Bob's router calls revert because `allowedSwapper[pool][router] == false`. The primary user-facing entry point is broken for all allowlisted users.
- **Path B – admin allowlists the router to fix Path A**: `allowedSwapper[pool][router] = true`. Now every unprivileged user (Charlie, etc.) can call `router.exactInputSingle(pool=curated_pool, ...)` and the extension passes, because it only checks whether the router is allowlisted. The allowlist is completely bypassed.

In Path B, unauthorized traders can execute swaps against a pool whose LP providers deposited under the assumption that only vetted counterparties would trade. This directly exposes LP principal to adversarial flow, satisfying the "direct loss of user principal" impact gate.

---

### Likelihood Explanation

The `MetricOmmSimpleRouter` is the canonical, production-grade entry point documented in the periphery README. Any pool admin who wants allowlisted users to be able to use the router will naturally allowlist the router address. The bypass requires no special privileges, no flash loans, and no exotic tokens — only a standard router call. Likelihood is **Medium** (requires the admin to allowlist the router, which is the natural remediation for Path A).

---

### Recommendation

The extension must recover the original user identity rather than relying on the `sender` argument forwarded by the pool. Two complementary fixes:

1. **Pass the original caller through `extensionData`**: The router encodes `msg.sender` into `extensionData` before calling the pool; the extension decodes and checks it. This requires a coordinated change in the router and extension.

2. **Check `sender` at the router level before calling the pool**: The router reads the allowlist and reverts before the pool call if the caller is not approved. This keeps the extension as a last-resort guard and adds a router-level gate.

The cleanest on-chain fix is option 1, because it preserves the extension as the single source of truth and does not require the router to know about every pool's allowlist configuration.

---

### Proof of Concept

```
Setup:
  - Deploy pool with SwapAllowlistExtension
  - allowedSwapper[pool][alice] = true   (Alice is the intended gated user)
  - allowedSwapper[pool][router] = true  (admin adds this so Alice can use the router)

Attack:
  1. Charlie (not allowlisted) calls:
       router.exactInputSingle({pool: curated_pool, ...})
  2. Router calls pool.swap(recipient=charlie, ...)
       → pool's msg.sender = router
  3. Pool calls _beforeSwap(sender=router, ...)
  4. Extension evaluates:
       allowedSwapper[pool][router] == true  → passes
  5. Swap executes. Charlie receives tokens from the curated pool.
     The allowlist guard was a no-op for Charlie's actual address.
``` [4](#0-3) [5](#0-4) [1](#0-0)

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
