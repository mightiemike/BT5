### Title
SwapAllowlistExtension Checks Router Address Instead of End-User, Allowing Any User to Bypass Per-User Swap Restrictions via MetricOmmSimpleRouter — (`metric-periphery/contracts/extensions/SwapAllowlistExtension.sol`)

---

### Summary

`SwapAllowlistExtension.beforeSwap` gates swaps by checking the `sender` argument passed from the pool, which is always `msg.sender` of the pool's `swap()` call. When users route through `MetricOmmSimpleRouter`, the router is the immediate caller of the pool, so the extension checks the router's address against the allowlist — not the end-user's address. If the pool admin allowlists the router to support router-mediated swaps, every user on the network can bypass the per-user allowlist by routing through the router.

---

### Finding Description

**Call chain:**

1. User calls `MetricOmmSimpleRouter.exactInputSingle()` (or `exactInput`, `exactOutputSingle`, `exactOutput`).
2. The router calls `IMetricOmmPoolActions(pool).swap(recipient, ...)` directly — `msg.sender` of the pool's `swap` is the **router address**.
3. `MetricOmmPool.swap` passes `msg.sender` (the router) as `sender` to `_beforeSwap`:

```solidity
// MetricOmmPool.sol line 230-240
_beforeSwap(
    msg.sender,   // <-- router address, not the end-user
    recipient,
    ...
);
```

4. `ExtensionCalling._beforeSwap` forwards this `sender` to `SwapAllowlistExtension.beforeSwap`.
5. The extension checks:

```solidity
// SwapAllowlistExtension.sol line 37
if (!allowAllSwappers[msg.sender] && !allowedSwapper[msg.sender][sender]) {
    revert IMetricOmmPoolActions.NotAllowedToSwap();
}
```

Here `msg.sender` is the pool and `sender` is the router. The check becomes `allowedSwapper[pool][router]`.

**The broken invariant:** The extension is designed to gate individual swappers, but it cannot distinguish between "allowlisted user going through the router" and "non-allowlisted user going through the router". Both appear as the same `sender` (the router address).

**The bypass path:** A pool admin who wants to support router-mediated swaps for their allowlisted users must add the router to the allowlist. Once the router is allowlisted, `allowedSwapper[pool][router] == true`, and the check passes for **any** user who routes through the router — the per-user allowlist is completely bypassed.

---

### Impact Explanation

Any user can execute swaps on a curated pool that was intended to restrict access to specific addresses. Consequences include:

- **Policy bypass:** KYC/compliance-gated pools become open to the public via the router.
- **Direct fund loss:** Unauthorized users can drain LP-owned token reserves by swapping at oracle-anchored prices on pools that were never intended to be public.
- **Broken core pool functionality:** The pool's primary access-control extension fails to enforce its configured invariant on the supported periphery path.

---

### Likelihood Explanation

The bypass requires the pool admin to allowlist the router address. This is a natural and expected configuration step: any pool admin who deploys a `SwapAllowlistExtension`-gated pool and also wants their allowlisted users to be able to use the standard `MetricOmmSimpleRouter` must add the router to the allowlist. The protocol provides no warning that doing so opens the gate to all users. The router is a first-party, factory-registered contract, making this a foreseeable and likely misconfiguration.

---

### Recommendation

**Short term:** In `SwapAllowlistExtension.beforeSwap`, do not rely solely on the `sender` argument (the immediate pool caller). Add a mechanism for the router to forward the originating user's address — for example, via the `extensionData` bytes — and verify that forwarded identity against the allowlist. Alternatively, document explicitly that allowlisting the router grants access to all users and that per-user gating is only enforceable for direct pool calls.

**Long term:** Redesign the allowlist extension to accept a verified caller identity from trusted periphery contracts (e.g., via a signed message or a registry of trusted forwarders), so that the allowlist policy is enforced uniformly regardless of which supported entrypoint reaches the pool.

---

### Proof of Concept

```
Setup:
  - Deploy pool with SwapAllowlistExtension configured as beforeSwap hook.
  - Pool admin calls setAllowedToSwap(pool, alice, true)       // alice is allowlisted
  - Pool admin calls setAllowedToSwap(pool, router, true)      // router allowlisted to support alice's router usage

Attack:
  - Bob (not allowlisted) calls MetricOmmSimpleRouter.exactInputSingle({pool: pool, ...})
  - Router calls pool.swap(recipient, ...) — msg.sender = router
  - Pool calls extension.beforeSwap(router, ...) — sender = router
  - Extension checks: allowedSwapper[pool][router] == true  ✓  → passes
  - Bob's swap executes on the curated pool despite never being allowlisted.

Result:
  - Bob receives pool output tokens.
  - Pool LP reserves are reduced by an unauthorized swap.
  - The SwapAllowlistExtension guard is silently bypassed.
```

**Relevant code locations:** [1](#0-0) [2](#0-1) [3](#0-2)

### Citations

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

**File:** metric-core/contracts/MetricOmmPool.sol (L230-241)
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
