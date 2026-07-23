### Title
SwapAllowlistExtension Bypass via MetricOmmSimpleRouter — Allowlist Gates Router Address Instead of End User - (File: metric-periphery/contracts/extensions/SwapAllowlistExtension.sol)

---

### Summary

`SwapAllowlistExtension.beforeSwap` checks the `sender` argument passed by the pool, which is `msg.sender` of `pool.swap()`. When a user routes through `MetricOmmSimpleRouter`, `msg.sender` to the pool is the **router contract**, not the end user. If the router is allowlisted (a natural admin action to enable router-mediated swaps), every user — including those explicitly blocked — can bypass the per-user allowlist by routing through the public router.

---

### Finding Description

`MetricOmmPool.swap()` passes `msg.sender` as the `sender` argument to `_beforeSwap`: [1](#0-0) 

`ExtensionCalling._beforeSwap` forwards that value unchanged to the extension: [2](#0-1) 

`SwapAllowlistExtension.beforeSwap` then checks `allowedSwapper[msg.sender][sender]`, where `msg.sender` is the pool and `sender` is the immediate caller of `pool.swap()`: [3](#0-2) 

When a user calls `MetricOmmSimpleRouter.exactInput*()`, the router calls `pool.swap()` on the user's behalf. At that point `msg.sender` to the pool is the **router address**, so `sender` passed to the extension is the router, not the end user. The allowlist lookup becomes `allowedSwapper[pool][router]` — completely decoupled from the actual trader's identity.

This creates an irresolvable dilemma for the pool admin:

| Admin configuration | Direct swap by blocked user | Router swap by blocked user |
|---|---|---|
| Allowlist specific users only | Correctly blocked | Also blocked (router not listed) — **breaks legitimate router use for allowlisted users too** |
| Allowlist the router (to enable router path) | Correctly blocked | **Bypassed — any user passes** |

There is no configuration that simultaneously allows allowlisted users to swap via the router while blocking non-allowlisted users from doing the same.

The analog to the stream-update bug is exact: just as `updateStream()` applied the wrong time-proportional accounting to a new top-up (using stale `startTime` instead of resetting the baseline), `SwapAllowlistExtension` applies the allowlist check to the wrong identity (the router's address instead of the end user's address), causing the guard to be misapplied with fund-impacting consequences.

---

### Impact Explanation

A pool admin deploys a permissioned pool (e.g., KYC-only, institutional-only) using `SwapAllowlistExtension`. Any non-allowlisted user can bypass the restriction by calling `MetricOmmSimpleRouter` instead of the pool directly. The router is a public, permissionless contract. The bypassing user can execute arbitrary swaps against LP positions — extracting value from LPs, manipulating pool state, or violating the access-control invariant the admin intended to enforce. This is a direct loss of the pool's access-control guarantee and enables unauthorized fund flows against LP principal.

---

### Likelihood Explanation

- `MetricOmmSimpleRouter` is a public periphery contract designed for general use.
- Any user who discovers the allowlist blocks their direct swap will naturally try the router as an alternative path.
- The admin allowlisting the router is a predictable and necessary action to support normal router-mediated trading for legitimate users.
- No privileged access, special tokens, or unusual setup is required — only a standard router call.

---

### Recommendation

The extension must check the **end user's identity**, not the immediate caller of `pool.swap()`. Two options:

1. **Pass the original user through `extensionData`**: require the router to encode the real user address in `extensionData` and have the extension decode and verify it. This requires router cooperation and is fragile.

2. **Check `sender` against the allowlist only when `sender` is not a trusted router; otherwise check the user encoded in `extensionData`**: still fragile.

3. **Preferred — gate at the router level**: the `SwapAllowlistExtension` should expose a view function `isAllowedToSwap(pool, user)` that the router calls before forwarding, and the extension's `beforeSwap` should only be used for direct pool calls. Alternatively, redesign the hook signature to carry the original initiator separately from the immediate caller.

---

### Proof of Concept

```
Setup:
  - Deploy pool with SwapAllowlistExtension
  - Admin allowlists the router: swapExtension.setAllowedToSwap(pool, address(router), true)
  - blockedUser is NOT in the allowlist

Attack:
  1. blockedUser calls pool.swap() directly
     → sender = blockedUser → allowedSwapper[pool][blockedUser] = false → REVERT ✓ (guard works)

  2. blockedUser calls router.exactInputSingle(pool, ...)
     → router calls pool.swap()
     → msg.sender to pool = router
     → sender passed to extension = router
     → allowedSwapper[pool][router] = true → PASSES ✗ (guard bypassed)
     → swap executes, blockedUser receives output tokens
``` [4](#0-3) [5](#0-4)

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
