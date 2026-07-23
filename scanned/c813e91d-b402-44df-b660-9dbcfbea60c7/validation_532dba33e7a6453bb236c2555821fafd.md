### Title
`SwapAllowlistExtension.beforeSwap` gates the router address instead of the actual user, allowing any user to bypass the swap allowlist via the router — (`metric-periphery/contracts/extensions/SwapAllowlistExtension.sol`)

---

### Summary

`SwapAllowlistExtension` is designed to restrict which addresses may swap on a curated pool. Its `beforeSwap` hook checks the `sender` argument — which is `msg.sender` of the pool's `swap()` call — against the per-pool allowlist. When users route through `MetricOmmSimpleRouter`, `sender` equals the **router address**, not the actual user. If the pool admin allowlists the router (a natural step to enable router-mediated swaps), every user — including explicitly non-allowlisted ones — can bypass the guard by routing through the router.

---

### Finding Description

In `MetricOmmPool.swap`, the pool passes `msg.sender` as the `sender` argument to `_beforeSwap`: [1](#0-0) 

`ExtensionCalling._beforeSwap` forwards this value as the first positional argument to every configured extension: [2](#0-1) 

`SwapAllowlistExtension.beforeSwap` then checks that first argument (`sender`) against the per-pool allowlist: [3](#0-2) 

When a user calls the pool **directly**, `sender = user` — the check is correct. When a user calls through `MetricOmmSimpleRouter`, the router calls `pool.swap(...)`, so `sender = router`. The extension then evaluates `allowedSwapper[pool][router]`, not `allowedSwapper[pool][user]`.

The pool admin must allowlist the router to permit any router-mediated swap. Once the router is allowlisted, **every user** — regardless of their individual allowlist status — can bypass the guard by routing through the router. The pool admin has no mechanism to simultaneously (a) allow router-mediated swaps and (b) enforce per-user restrictions, because the hook irrevocably conflates the router identity with the user identity.

This is the direct analog of the seeded bug: just as a strategist's privilege could be granted but never revoked, a user's swap access cannot be effectively revoked on a pool that supports router-mediated swaps, because the allowlist gates the intermediary rather than the economic actor.

---

### Impact Explanation

A curated pool deploying `SwapAllowlistExtension` to restrict trading to a specific set of addresses loses that restriction entirely for any user who routes through the supported periphery router. Unauthorized users can execute swaps, draining LP value at oracle-anchored prices without the pool admin's consent. The allowlist invariant — "only approved addresses may swap" — is broken on the primary supported swap path.

---

### Likelihood Explanation

The pool admin must allowlist the router for this to be exploitable. However, allowlisting the router is the **expected** configuration for any curated pool that also wants to support the standard periphery swap path. The admin has no way to know that doing so opens the gate to all users, because the extension's `setAllowedToSwap` / `isAllowedToSwap` API presents a per-user interface that implies per-user enforcement. Any pool that configures both `SwapAllowlistExtension` and router support is vulnerable.

---

### Recommendation

The `beforeSwap` hook must gate the **actual user**, not the intermediary. Two options:

1. **Check `recipient` instead of `sender`** — `recipient` is the address that receives output tokens and is typically the real user. However, `recipient` can be set to any address by the router, so this is only safe if the router is trusted to set `recipient = actual user`.

2. **Require the router to forward the real user in `extensionData`** — The extension decodes the actual user from `extensionData` and checks that address. The router must be updated to encode the original `msg.sender` into `extensionData` before calling the pool.

Option 2 is more robust. The `beforeSwap` signature already receives `extensionData` as the last argument: [3](#0-2) 

The extension should decode the real user from `extensionData` when `sender` is a known router, or the pool/router architecture should be updated so the actual initiating user is always the checked identity.

---

### Proof of Concept

```
Setup:
  - Deploy pool with SwapAllowlistExtension
  - Pool admin allowlists the router: setAllowedToSwap(pool, router, true)
  - Pool admin does NOT allowlist Alice: allowedSwapper[pool][alice] = false

Attack:
  1. Alice calls MetricOmmSimpleRouter.exactInput(...)
  2. Router calls pool.swap(recipient=alice, ...)
  3. Pool calls _beforeSwap(sender=router, recipient=alice, ...)
  4. SwapAllowlistExtension checks allowedSwapper[pool][router] == true → passes
  5. Alice's swap executes despite never being allowlisted

Result:
  - Alice swaps on a pool she was explicitly excluded from
  - Pool admin cannot revoke Alice's effective access without also blocking all router users
```

The root cause is at: [4](#0-3) 

where `sender` is the router, not the user, when the swap is router-mediated.

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
