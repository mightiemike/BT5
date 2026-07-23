### Title
`SwapAllowlistExtension` Checks Router Address Instead of End-User, Allowing Allowlist Bypass via `MetricOmmSimpleRouter` — (`metric-periphery/contracts/extensions/SwapAllowlistExtension.sol`)

---

### Summary

`SwapAllowlistExtension.beforeSwap` gates swaps by checking `allowedSwapper[pool][sender]`, where `sender` is the `msg.sender` of the pool's `swap()` call. When a user routes through `MetricOmmSimpleRouter`, the pool's `msg.sender` is the **router**, not the end user. If a pool admin allowlists the router (a natural action to enable standard periphery usage), every user — including those not on the allowlist — can bypass the curation gate by routing through the router.

---

### Finding Description

**Call path:**

1. User calls `MetricOmmSimpleRouter.exactInputSingle()` (or `exactInput`, `exactOutputSingle`, `exactOutput`).
2. The router calls `IMetricOmmPoolActions(pool).swap(recipient, zeroForOne, amount, priceLimit, "", extensionData)` — here `msg.sender` to the pool is the **router address**.
3. `MetricOmmPool.swap` calls `_beforeSwap(msg.sender, ...)` where `msg.sender` is the router.
4. `ExtensionCalling._beforeSwap` encodes and dispatches `IMetricOmmExtensions.beforeSwap(sender=router, ...)` to every configured extension.
5. `SwapAllowlistExtension.beforeSwap` evaluates:

```solidity
if (!allowAllSwappers[msg.sender] && !allowedSwapper[msg.sender][sender]) {
    revert IMetricOmmPoolActions.NotAllowedToSwap();
}
```

Here `msg.sender` is the pool and `sender` is the router. The check passes if `allowedSwapper[pool][router]` is `true`.

6. If the pool admin has allowlisted the router (so that legitimate users can use the standard periphery), the check passes for **every caller** regardless of whether they are individually allowlisted.

The same bypass applies to multi-hop `exactInput` (all hops see `sender = router`) and to the recursive `exactOutput` path (intermediate hops are called from within the router's callback, so `msg.sender` to each pool is still the router).

**Root cause:** The allowlist is keyed on the immediate caller of `pool.swap()`, not on the economic actor initiating the trade. The router is a transparent forwarder — it does not enforce per-user allowlist checks before calling the pool.

---

### Impact Explanation

A pool admin who deploys a `SwapAllowlistExtension` intends to restrict trading to a curated set of addresses. To also allow those addresses to use the standard periphery, the admin must allowlist the router. Doing so silently opens the pool to **all** callers: any address can call `MetricOmmSimpleRouter.exactInputSingle` and the extension will pass because it sees the router, not the individual user.

Consequences:
- Unauthorized users trade on a pool that was designed to be curated (e.g., institutional-only, KYC-gated, or partner-restricted).
- The pool admin's access-control boundary is broken by an unprivileged path (the public router).
- In pools where the allowlist is the primary defense against adversarial flow (e.g., paired with a stop-loss extension), bypassing it removes the first line of protection and exposes LP funds to unintended counterparties.

---

### Likelihood Explanation

**Low-to-Medium.** The bypass requires the pool admin to allowlist the router. This is a natural and expected configuration step: without it, allowlisted users cannot use the standard periphery at all. The admin is likely to make this configuration choice without realizing it grants universal access. The router is a public, permissionless contract, so once the router is allowlisted, the bypass is trivially reachable by any EOA.

---

### Recommendation

The allowlist must gate the **end user**, not the immediate caller. Two approaches:

1. **Pass the original user through the router.** The router could forward `msg.sender` as an additional field in `extensionData`, and the extension could decode and check it. This requires a coordinated change to the router and extension.

2. **Check `sender` against a router registry and fall back to the original user.** If `sender` is a known router, the extension should require the actual user identity to be supplied in `extensionData` and verified (e.g., via a signed permit or explicit parameter).

3. **Document that allowlisting the router is equivalent to `allowAllSwappers = true`** and provide a separate mechanism for per-user gating through the router (e.g., a router that enforces its own allowlist before calling the pool).

---

### Proof of Concept

```
Setup:
  - Deploy pool with SwapAllowlistExtension.
  - Pool admin calls setAllowedToSwap(pool, alice, true)   // alice is allowed
  - Pool admin calls setAllowedToSwap(pool, router, true)  // router allowlisted so alice can use periphery

Attack:
  - Bob (not allowlisted) calls MetricOmmSimpleRouter.exactInputSingle({pool: pool, ...})
  - Router calls pool.swap(recipient, zeroForOne, amount, priceLimit, "", extensionData)
    → msg.sender to pool = router
  - Pool calls _beforeSwap(sender=router, ...)
  - SwapAllowlistExtension checks allowedSwapper[pool][router] → true
  - Swap executes successfully for Bob despite Bob not being on the allowlist.

Expected: revert NotAllowedToSwap
Actual:   swap succeeds
```

**Relevant code locations:** [1](#0-0) [2](#0-1) [3](#0-2) [4](#0-3) [5](#0-4)

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
