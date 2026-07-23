### Title
`SwapAllowlistExtension` Gates the Router Address Instead of the Actual Swapper, Allowing Any User to Bypass the Configured Allowlist via the Router — (`metric-periphery/contracts/extensions/SwapAllowlistExtension.sol`)

---

### Summary

`SwapAllowlistExtension.beforeSwap` checks the `sender` argument forwarded by the pool, which is always `msg.sender` of `pool.swap()`. When a user routes through `MetricOmmSimpleRouter`, that `msg.sender` is the **router contract**, not the actual user. If the pool admin allowlists the router (which is required for any router-mediated swap to succeed), every user — including those the admin intended to block — can bypass the allowlist by calling the router instead of the pool directly.

---

### Finding Description

**Root cause — `MetricOmmPool.swap` passes `msg.sender` as `sender`:** [1](#0-0) 

The pool unconditionally passes `msg.sender` as the first argument to `_beforeSwap`. There is no mechanism to propagate the original end-user address through the router.

**Root cause — `SwapAllowlistExtension.beforeSwap` checks `sender` (the router) not the real user:** [2](#0-1) 

`msg.sender` inside the extension is the pool (correct). `sender` is whatever the pool forwarded — the router address when the call originates from `MetricOmmSimpleRouter`. The check therefore evaluates `allowedSwapper[pool][router]`, not `allowedSwapper[pool][realUser]`.

**Router call path — `exactInputSingle` calls `pool.swap` as `msg.sender = router`:** [3](#0-2) 

The router calls `pool.swap(recipient, ...)` directly. The pool records `msg.sender = router` and forwards it to every extension hook. The real caller's identity is lost.

**Multi-hop path has the same flaw:** [4](#0-3) 

For intermediate hops, `msg.sender` stored in the callback context is `address(this)` (the router itself), so the extension still sees the router, not the user.

---

### Impact Explanation

The pool admin configures `SwapAllowlistExtension` to restrict swaps to a specific set of addresses (e.g., KYC-verified counterparties, institutional traders, or whitelisted protocols). The invariant is: **only allowlisted addresses may swap**.

Because the extension checks the immediate caller of `pool.swap()`, the admin faces an impossible choice:

| Admin action | Result |
|---|---|
| Do **not** allowlist the router | Allowlisted users cannot use the router at all |
| **Allowlist the router** | Every user — allowlisted or not — can bypass the gate via the router |

In the second (operationally necessary) case, any non-allowlisted user calls `router.exactInputSingle(...)`, the extension sees `allowedSwapper[pool][router] = true`, and the swap proceeds. The configured guard is completely neutralized. This is a direct admin-boundary break: an unprivileged actor bypasses a pool-admin-configured access control with fund-flow consequences (unauthorized swaps execute against pool liquidity).

---

### Likelihood Explanation

- The router (`MetricOmmSimpleRouter`) is a public, permissionless contract — any EOA or contract can call it.
- A pool admin who deploys `SwapAllowlistExtension` almost certainly intends for allowlisted users to use the router (the primary UX entry point). Allowlisting the router is the natural operational step, which immediately opens the bypass to everyone.
- No special privilege, flash loan, or oracle manipulation is required. A single `exactInputSingle` call suffices.

---

### Recommendation

The extension must verify the **original end-user**, not the immediate caller of `pool.swap()`. Two viable approaches:

1. **Pass the real sender through `extensionData`**: Have the router ABI-encode `msg.sender` into `extensionData` before forwarding to the pool. The extension decodes and checks that address. This requires a convention between the router and the extension but needs no pool changes.

2. **Check `sender` only for direct pool calls; require `extensionData` attestation for router calls**: The extension can detect that `sender` is a known router and require a signed or encoded real-user identity in `extensionData`.

3. **Restrict direct pool access**: Require all swaps to go through a router that embeds the real sender in a verifiable way, and remove the router from the allowlist in favor of per-user entries that the router resolves on-chain.

---

### Proof of Concept

```
Setup:
  pool configured with SwapAllowlistExtension
  allowedSwapper[pool][alice] = true   // alice is the only allowed swapper
  allowedSwapper[pool][router] = true  // admin must set this for alice to use the router

Attack (executed by bob, who is NOT allowlisted):
  bob calls router.exactInputSingle({pool: pool, ...})
    → router calls pool.swap(recipient, ...)   // msg.sender = router
    → pool calls _beforeSwap(sender=router, ...)
    → SwapAllowlistExtension checks allowedSwapper[pool][router] → TRUE
    → swap executes successfully for bob

Result:
  bob swaps against pool liquidity despite never being allowlisted.
  The SwapAllowlistExtension guard is fully bypassed.
``` [2](#0-1) [1](#0-0) [3](#0-2)

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
