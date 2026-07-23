### Title
`SwapAllowlistExtension` Checks Router Address Instead of Original User, Allowing Any User to Bypass Per-User Swap Allowlist — (`metric-periphery/contracts/extensions/SwapAllowlistExtension.sol`)

---

### Summary

`SwapAllowlistExtension.beforeSwap` gates swaps by checking the `sender` argument passed by the pool. The pool always passes `msg.sender` (the direct caller of `pool.swap`) as `sender`. When a user routes through `MetricOmmSimpleRouter`, the pool's `msg.sender` is the **router contract**, not the original user. The extension therefore checks whether the **router** is allowlisted, not whether the **user** is allowlisted. Any user can bypass a per-user swap allowlist on a curated pool by routing through the public router.

---

### Finding Description

**Invariant broken:** A configured swap allowlist must gate the same actor the pool admin intended to restrict, regardless of which supported public entrypoint reaches the pool.

**Root cause — wrong actor binding in `SwapAllowlistExtension.beforeSwap`:**

`MetricOmmPool.swap` passes `msg.sender` as the `sender` argument to `_beforeSwap`: [1](#0-0) 

`SwapAllowlistExtension.beforeSwap` then checks `allowedSwapper[msg.sender][sender]`, where `msg.sender` is the pool and `sender` is whoever called `pool.swap`: [2](#0-1) 

When a user routes through `MetricOmmSimpleRouter.exactInputSingle` (or `exactInput`), the router is the direct caller of `pool.swap`: [3](#0-2) 

So the extension evaluates `allowedSwapper[pool][router]` — the router's allowlist status — not the original user's. The pool admin's per-user allowlist entries (e.g., `allowedSwapper[pool][alice] = true`) are never consulted for router-mediated swaps.

**Two failure modes result:**

| Scenario | Effect |
|---|---|
| Router is **not** allowlisted | Allowlisted users cannot swap through the router; core swap functionality is broken for them |
| Router **is** allowlisted | Every user on the network can swap through the router, completely bypassing the per-user allowlist |

Neither configuration lets the pool admin enforce per-user restrictions while still supporting the public router.

---

### Impact Explanation

A pool configured with `SwapAllowlistExtension` to restrict swaps to specific addresses (e.g., KYC-verified counterparties, institutional LPs, or protocol-controlled addresses) can be fully bypassed by any unprivileged user routing through the public `MetricOmmSimpleRouter`. The attacker receives pool output tokens and the pool receives input tokens — a real economic swap executes against a pool that was supposed to be restricted. This is a direct policy bypass with fund-impacting consequences: unauthorized users trade in a curated pool, potentially draining LP value or violating regulatory/contractual restrictions the pool admin intended to enforce.

---

### Likelihood Explanation

- `MetricOmmSimpleRouter` is a public, permissionless contract — any user can call it with no preconditions.
- The bypass requires only a single transaction: call `exactInputSingle` or `exactInput` on the router targeting the restricted pool.
- No privileged access, no malicious setup, no special token behavior required.
- The pool admin has no on-chain mechanism to prevent router-mediated swaps while keeping the router usable for allowlisted users.

---

### Recommendation

The extension must gate the **original user**, not the intermediary. Two approaches:

1. **Pass original caller through `extensionData`:** The router encodes `msg.sender` into `extensionData`; the extension decodes and checks it. This requires a coordinated change to the router and extension.

2. **Check `sender` against a router registry and fall back to `tx.origin` or a signed proof:** Fragile and generally inadvisable.

3. **Preferred — check `sender` and, if `sender` is a known router, require the extension payload to carry a signed or factory-verified user identity.** The extension's `beforeSwap` should verify the economically relevant actor, not the transport layer.

The simplest safe fix: `SwapAllowlistExtension.beforeSwap` should revert if `sender` is not itself allowlisted **and** there is no mechanism to recover the original user identity. Pool admins should be warned that allowlisting the router grants access to all users.

---

### Proof of Concept

```
Setup:
  - Deploy pool with SwapAllowlistExtension configured
  - Pool admin calls setAllowedToSwap(pool, alice, true)   // only alice should swap
  - Pool admin does NOT allowlist the router
  - Pool admin does NOT call setAllowAllSwappers(pool, true)

Attack (as Bob, a non-allowlisted user):
  1. Bob calls MetricOmmSimpleRouter.exactInputSingle({
       pool: restrictedPool,
       recipient: bob,
       zeroForOne: true,
       amountIn: X,
       ...
     })
  2. Router calls restrictedPool.swap(bob, true, X, ...)
     → pool.msg.sender = router
  3. Pool calls _beforeSwap(router, bob, ...)
  4. SwapAllowlistExtension.beforeSwap checks:
       allowedSwapper[pool][router]  → false (router not allowlisted)
     → REVERTS with NotAllowedToSwap

Alternate attack (router allowlisted):
  - Pool admin calls setAllowedToSwap(pool, router, true)
    (forced to do this so alice can use the router)
  - Now Bob calls router.exactInputSingle targeting the pool
  - Extension checks allowedSwapper[pool][router] → true → PASSES
  - Bob's swap executes on the supposedly restricted pool
  - Allowlist is completely bypassed for all users
```

The `sender` identity mismatch — router address checked instead of original user — is the direct analog of the SENDALL self-transfer invariant failure: the guard operates on the wrong identity and therefore fails to protect the intended boundary.

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
