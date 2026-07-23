### Title
`SwapAllowlistExtension` gates the router address instead of the actual user, allowing any user to bypass a curated pool's swap allowlist via `MetricOmmSimpleRouter` — (`metric-periphery/contracts/extensions/SwapAllowlistExtension.sol`)

---

### Summary

`SwapAllowlistExtension.beforeSwap` checks the `sender` argument, which the pool sets to `msg.sender` of the `pool.swap()` call. When a swap is routed through `MetricOmmSimpleRouter`, `msg.sender` to the pool is the router contract, not the end user. If the router is allowlisted (a necessary step for any router-mediated swap to succeed on a curated pool), every user who can call the router bypasses the per-user allowlist entirely.

---

### Finding Description

**Actor binding in `SwapAllowlistExtension`**

`SwapAllowlistExtension.beforeSwap` receives `sender` as its first argument and checks it against the per-pool allowlist:

```solidity
// SwapAllowlistExtension.sol line 37
if (!allowAllSwappers[msg.sender] && !allowedSwapper[msg.sender][sender]) {
    revert IMetricOmmPoolActions.NotAllowedToSwap();
}
```

`msg.sender` here is the pool (correct for the pool-keyed mapping). `sender` is the first argument forwarded by the pool, which is set to `msg.sender` of the `pool.swap()` call:

```solidity
// MetricOmmPool.sol line 230-240
_beforeSwap(
    msg.sender,   // <-- whoever called pool.swap()
    recipient,
    ...
);
```

**Router path**

When a user calls `MetricOmmSimpleRouter.exactInputSingle(...)`, the router calls `pool.swap(...)` directly:

```solidity
// MetricOmmSimpleRouter.sol line 72-80
(int128 amount0Delta, int128 amount1Delta) = IMetricOmmPoolActions(params.pool)
    .swap(
        params.recipient,
        params.zeroForOne,
        ...
    );
```

The pool's `msg.sender` is the router contract address. The extension therefore checks `allowedSwapper[pool][router]`, not `allowedSwapper[pool][actual_user]`.

**The bypass**

A pool admin who wants to support router-mediated swaps must allowlist the router address. Once the router is allowlisted, the check `allowedSwapper[pool][router]` passes for every user who calls through the router, regardless of whether that individual user is on the allowlist. The per-user curation is completely defeated.

This is structurally identical to the M-02 pattern: the safeguard (`allowedSwapper` per-user check) can be bypassed through a legitimate admin action (allowlisting the router), after which the guard fails open for all users.

The same issue applies to the multi-hop `exactInput` path for intermediate hops, where the router passes `address(this)` as the payer and calls `pool.swap(...)` from itself:

```solidity
// MetricOmmSimpleRouter.sol line 103-112
_setNextCallbackContext(pool, CALLBACK_MODE_JUST_PAY, i == 0 ? msg.sender : address(this), params.tokens[i]);
(int128 amount0Delta, int128 amount1Delta) = IMetricOmmPoolActions(pool).swap(...);
```

---

### Impact Explanation

A pool configured with `SwapAllowlistExtension` to restrict swaps to specific counterparties (e.g., KYC'd market makers, institutional LPs, or whitelisted protocols) is fully open to any user who routes through `MetricOmmSimpleRouter` once the router is allowlisted. Unauthorized users can execute swaps against the pool's liquidity, causing LP value loss through adverse selection and violating the pool's intended access control policy. This is a direct loss of LP principal above Sherlock thresholds if the pool holds significant liquidity.

---

### Likelihood Explanation

The router is the canonical user-facing entry point for swaps. Any pool admin who deploys a curated pool and also wants to support the standard periphery router must allowlist the router. This is a predictable and common configuration. The bypass requires no special privileges — any user can call the public router functions.

---

### Recommendation

The `SwapAllowlistExtension` must gate on the economically relevant actor, not the immediate caller of `pool.swap()`. Two options:

1. **Gate on `recipient`** — if the pool's intent is to restrict who receives output tokens, check `recipient` instead of `sender`. However, `recipient` is also caller-supplied and can be set to any address.

2. **Require direct pool calls for curated pools** — document that `SwapAllowlistExtension` is incompatible with router-mediated swaps and enforce this by checking that `sender` is not a known router. This is fragile.

3. **Preferred: pass the originating user through `extensionData`** — the router should encode `msg.sender` (the actual user) into `extensionData`, and the extension should decode and check that value. This requires a coordinated change to the router and extension, but correctly binds the guard to the actual economic actor.

---

### Proof of Concept

1. Pool admin deploys a pool with `SwapAllowlistExtension` configured.
2. Pool admin calls `setAllowedToSwap(pool, alice, true)` — only Alice is allowed to swap.
3. Pool admin calls `setAllowedToSwap(pool, router, true)` — necessary for router-mediated swaps.
4. Bob (not allowlisted) calls `MetricOmmSimpleRouter.exactInputSingle(...)` targeting the pool.
5. Router calls `pool.swap(...)` with `msg.sender = router`.
6. Pool calls `_beforeSwap(router, ...)`.
7. Extension checks `allowedSwapper[pool][router]` → `true` → swap proceeds.
8. Bob successfully swaps on a pool he was supposed to be excluded from.

The guard at [1](#0-0)  checks `sender`, which the pool sets to `msg.sender` of `pool.swap()` at [2](#0-1)  — the router address, not the end user. The router calls `pool.swap()` directly at [3](#0-2) , so the extension never sees the actual user. The multi-hop path has the same flaw at [4](#0-3) . The `ExtensionCalling._beforeSwap` dispatcher faithfully forwards `msg.sender` as `sender` at [5](#0-4) , confirming the wrong actor reaches the guard.

### Citations

**File:** metric-periphery/contracts/extensions/SwapAllowlistExtension.sol (L37-39)
```text
    if (!allowAllSwappers[msg.sender] && !allowedSwapper[msg.sender][sender]) {
      revert IMetricOmmPoolActions.NotAllowedToSwap();
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

**File:** metric-periphery/contracts/MetricOmmSimpleRouter.sol (L72-80)
```text
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

**File:** metric-core/contracts/ExtensionCalling.sol (L160-176)
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
```
