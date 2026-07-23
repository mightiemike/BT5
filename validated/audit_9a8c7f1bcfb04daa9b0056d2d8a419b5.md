### Title
`SwapAllowlistExtension` Gates on Router Address Instead of Actual User, Enabling Full Allowlist Bypass — (File: `metric-periphery/contracts/extensions/SwapAllowlistExtension.sol`)

---

### Summary

`SwapAllowlistExtension.beforeSwap` checks the identity of the **immediate caller of the pool** (`sender` = `msg.sender` of `pool.swap()`), not the actual end user. When swaps are routed through `MetricOmmSimpleRouter`, the pool sees `msg.sender = router`, so the extension checks whether the **router** is allowlisted. If the pool admin allowlists the router to enable router-mediated swaps for their permitted users, every unprivileged user can bypass the per-user restriction by routing through the same public router.

---

### Finding Description

**Step 1 — Pool passes its own `msg.sender` as `sender` to the extension.**

In `MetricOmmPool.swap()`, the `sender` argument forwarded to `_beforeSwap` is unconditionally `msg.sender` of the pool call: [1](#0-0) 

**Step 2 — Extension checks `sender` (the router) against the allowlist.**

`SwapAllowlistExtension.beforeSwap` uses `msg.sender` (the pool, enforced by `onlyPool`) as the mapping key and `sender` (the immediate caller of the pool) as the identity to gate: [2](#0-1) 

**Step 3 — The router calls `pool.swap()` directly, making itself the `sender`.**

`MetricOmmSimpleRouter.exactInputSingle` calls `pool.swap(params.recipient, ...)` with no mechanism to forward the actual user's identity: [3](#0-2) 

The pool therefore sees `msg.sender = router`, and the extension evaluates `allowedSwapper[pool][router]` — not `allowedSwapper[pool][actualUser]`.

**Step 4 — The same mismatch exists for multi-hop `exactInput`.**

For intermediate hops, the payer is `address(this)` (the router itself), so the identity mismatch is structural across all router entry points: [4](#0-3) 

---

### Impact Explanation

A pool admin who deploys a pool with `SwapAllowlistExtension` intends to restrict swaps to a named set of addresses. To let those addresses use the public router (the standard UX path), the admin must allowlist the router address. Once `allowedSwapper[pool][router] = true`, **every caller of the router** — including addresses the admin never approved — passes the `beforeSwap` check. The allowlist is completely neutralised for all router-mediated swaps.

Concrete consequence: unauthorized traders can execute swaps against a pool that was designed to be restricted (e.g., a private institutional pool, a pool with a circuit-breaker allowlist, or a pool whose LP terms require KYC). Unauthorized swaps drain LP assets at oracle-derived prices, constituting a direct loss of LP principal and a broken core pool invariant.

---

### Likelihood Explanation

The trigger requires only two conditions, both of which are expected in normal operation:

1. A pool is deployed with `SwapAllowlistExtension` (the extension exists precisely for this use case).
2. The pool admin allowlists the router so that their permitted users can interact via the standard periphery — a natural and documented configuration step.

No privileged access, no malicious setup, and no non-standard tokens are required. Any EOA can call `MetricOmmSimpleRouter.exactInputSingle` with the target pool.

---

### Recommendation

The extension must gate on the **actual end user**, not the immediate pool caller. Two sound approaches:

1. **Signed identity forwarding via `extensionData`**: require the router to embed a user-signed attestation in `extensionData`; the extension verifies the signature and checks the recovered address against the allowlist.
2. **Separate router allowlist tier**: distinguish between "this router contract is a trusted forwarder" and "this user is an allowed swapper"; the extension should require both — the router must be a registered forwarder **and** the user identity (passed through `extensionData`) must be allowlisted.

The simplest safe fix is to reject any `sender` that is not itself an EOA-equivalent allowlisted address, and require allowlisted users to call the pool directly (implementing `metricOmmSwapCallback`) or through a router that cryptographically attests the originating user.

---

### Proof of Concept

```
1. Pool admin deploys MetricOmmPool with SwapAllowlistExtension as beforeSwap hook.
2. Admin calls: extension.setAllowedToSwap(pool, user1, true)
   — intent: only user1 may swap.
3. Admin calls: extension.setAllowedToSwap(pool, router, true)
   — intent: allow user1 to use the standard router UX.
4. Attacker (user2, never allowlisted) calls:
     router.exactInputSingle({pool: pool, recipient: user2, ...})
5. Router calls pool.swap(user2, ...) — pool sees msg.sender = router.
6. Pool calls _beforeSwap(router, user2, ...).
7. Extension evaluates: allowedSwapper[pool][router] == true → passes.
8. Swap executes. user2 receives output tokens from the restricted pool.
   Allowlist invariant broken; LP assets drained by an unauthorized party.
``` [5](#0-4) [6](#0-5) [3](#0-2)

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
