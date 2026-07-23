### Title
SwapAllowlistExtension Gates the Router Address Instead of the Actual Swapper, Allowing Any User to Bypass the Allowlist — (`metric-periphery/contracts/extensions/SwapAllowlistExtension.sol`)

---

### Summary

The `SwapAllowlistExtension.beforeSwap` hook receives `sender` as `msg.sender` of the pool. When a user routes through `MetricOmmSimpleRouter`, `msg.sender` at the pool is the router contract, not the end user. The allowlist is keyed by `(pool, sender)`, so it checks whether the **router** is an allowed swapper — not whether the **user** is. If the pool admin allowlists the router to enable router-mediated swaps, every user on the network can bypass the allowlist entirely.

---

### Finding Description

`MetricOmmPool.swap` passes `msg.sender` as the `sender` argument to `_beforeSwap`:

```solidity
// MetricOmmPool.sol L230-240
_beforeSwap(
    msg.sender,   // <-- this is the router when called via MetricOmmSimpleRouter
    recipient,
    ...
);
```

`ExtensionCalling._beforeSwap` forwards this value unchanged to every configured extension:

```solidity
// ExtensionCalling.sol L160-176
_callExtensionsInOrder(
    BEFORE_SWAP_ORDER,
    abi.encodeCall(
        IMetricOmmExtensions.beforeSwap,
        (sender, recipient, ...)  // sender = router address
    )
);
```

`SwapAllowlistExtension.beforeSwap` then performs an `allowedSwapper` lookup keyed by `(pool, sender)`. When the call originates from `MetricOmmSimpleRouter`, `sender` is the router's address. The extension never sees the originating EOA.

**Consequence:** A pool admin who wants to allow router-mediated swaps must allowlist the router address. Once the router is allowlisted, the `(pool, router)` entry passes for every user who routes through it — the per-user gate is completely bypassed. Conversely, if the admin does not allowlist the router, no user can swap through the router at all, breaking the standard periphery flow.

This is the direct analog to the external report's pattern: a guard is configured and appears active, but the identity it validates is not the economically relevant actor, so the guard is either universally bypassed or universally blocking.

---

### Impact Explanation

- Any non-allowlisted user can swap in a pool that is supposed to be restricted, simply by routing through `MetricOmmSimpleRouter`.
- LP providers who deployed capital into a restricted pool expecting only vetted counterparties now face unrestricted swap exposure: adverse-selection losses, unauthorized price impact, and extraction of favorable oracle-anchored pricing by parties the pool was designed to exclude.
- The allowlist extension provides a false sense of security — it appears configured and active, but the guard is misbound to the router identity rather than the user identity.

**Severity: Medium** — requires the pool admin to allowlist the router (a natural and expected operational step), but once done, the bypass is unconditional and unprivileged.

---

### Likelihood Explanation

- `MetricOmmSimpleRouter` is the standard swap entry point for end users; pool admins will routinely allowlist it.
- No special permissions, flash loans, or unusual token behavior are required — any EOA can call the router.
- The bypass is silent: the extension returns success, the pool emits no anomaly, and the swap settles normally.

---

### Recommendation

The `beforeSwap` hook should check the **originating user** rather than the immediate caller. Two approaches:

1. **Pass the original caller through extensionData**: The router encodes the true `msg.sender` into `extensionData` before calling the pool, and the extension reads it from there. This requires the router to be trusted to populate this field honestly.

2. **Check `recipient` or use a separate allowlist keyed on recipient**: For swap allowlists, the economically relevant actor is the recipient of output tokens. Keying the allowlist on `recipient` instead of `sender` is harder to spoof via routing.

3. **Dedicated router-aware allowlist**: Extend the extension to recognize the router as a transparent forwarder and extract the true caller from a standardized field in `extensionData`, validated by the router's own signature or a transient-storage handshake.

---

### Proof of Concept

```
1. Deploy pool with SwapAllowlistExtension configured as beforeSwap hook.
2. Pool admin calls allowedSwapper[pool][userA] = true  (only userA is allowed).
3. Pool admin calls allowedSwapper[pool][router] = true  (to enable router-mediated swaps for userA).
4. UserB (not in allowlist) calls MetricOmmSimpleRouter.exactInput(...) targeting the pool.
5. Router calls pool.swap(...) → msg.sender at pool = router address.
6. SwapAllowlistExtension.beforeSwap checks allowedSwapper[pool][router] → true → passes.
7. UserB's swap executes successfully despite not being allowlisted.
8. UserB extracts oracle-anchored pricing from a pool intended to be restricted.
```

**Corrupted invariant:** `allowedSwapper[pool][userB] == false` should prevent userB from swapping; instead the check resolves to `allowedSwapper[pool][router] == true`, and the guard is silently bypassed. [1](#0-0) [2](#0-1) [3](#0-2)

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

**File:** generate_scanned_questions.py (L655-663)
```python
        Target(
            short="swap allowlist gate",
            file_function="metric-periphery/contracts/extensions/SwapAllowlistExtension.sol::beforeSwap",
            entrypoint="metric-core/contracts/MetricOmmPool.sol::swap and metric-periphery/contracts/MetricOmmSimpleRouter.sol::exact*",
            call_path="public swap -> beforeSwap hook -> allowAll/allowedSwapper lookup keyed by pool and sender",
            values="the exact swapper identity checked by the hook and whether router-mediated swaps preserve that identity",
            control_hint="Because public users may enter through the router, the hook must gate the same actor the pool designers thought they were allowlisting.",
            validation_focus="Test direct swaps and router swaps on allowlisted pools and assert the hook cannot be bypassed by routing through an intermediate public contract.",
        ),
```
