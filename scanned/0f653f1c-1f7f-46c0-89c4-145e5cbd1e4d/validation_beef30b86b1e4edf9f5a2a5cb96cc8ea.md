### Title
`SwapAllowlistExtension` gates the router address instead of the end user, allowing any user to bypass the swap allowlist via `MetricOmmSimpleRouter` - (`metric-periphery/contracts/extensions/SwapAllowlistExtension.sol`)

---

### Summary

`SwapAllowlistExtension.beforeSwap` checks the `sender` argument, which is `msg.sender` of the pool's `swap()` call. When a swap is routed through `MetricOmmSimpleRouter`, `msg.sender` of `pool.swap()` is the router contract, not the end user. If the pool admin allowlists the router to support router-based swaps for their curated users, any non-allowlisted user can bypass the swap allowlist by routing through the router.

---

### Finding Description

The call chain for a router-mediated swap is:

```
EOA (non-allowlisted) → MetricOmmSimpleRouter.exactInputSingle()
    → pool.swap(recipient, ...) [msg.sender = router]
        → _beforeSwap(msg.sender=router, ...)
            → SwapAllowlistExtension.beforeSwap(sender=router, ...)
                → checks allowedSwapper[pool][router]  ← wrong actor
```

In `MetricOmmPool.swap`, the pool passes its own `msg.sender` as `sender` to the extension dispatcher:

```solidity
_beforeSwap(
    msg.sender,   // ← router address, not the EOA
    recipient,
    ...
);
```

`ExtensionCalling._beforeSwap` forwards this verbatim to the extension. `SwapAllowlistExtension.beforeSwap` then evaluates:

```solidity
if (!allowAllSwappers[msg.sender] && !allowedSwapper[msg.sender][sender]) {
    revert IMetricOmmPoolActions.NotAllowedToSwap();
}
```

Here `msg.sender` is the pool (correct) and `sender` is the router (wrong). The check resolves to `allowedSwapper[pool][router]`.

A pool admin who wants their allowlisted users to be able to swap via the standard router **must** add the router to the allowlist. Once `allowedSwapper[pool][router] = true`, the guard passes for every caller regardless of who the actual EOA is, because the extension only sees the router address as `sender` for all router-mediated swaps.

The same bypass applies to `exactInput`, `exactOutput`, and `exactOutputSingle` — all router entry points call `pool.swap()` with `msg.sender = router`.

---

### Impact Explanation

A curated pool deploying `SwapAllowlistExtension` to restrict trading to a specific set of addresses loses that restriction entirely for any user who routes through `MetricOmmSimpleRouter`. The attacker pays no special cost: they simply call the public router with the target pool address. The pool executes the swap at oracle-derived prices, transferring tokens out of the pool to the attacker's chosen recipient. This breaks the core curation invariant and constitutes unauthorized access to pool liquidity.

---

### Likelihood Explanation

The bypass requires the router to be allowlisted. This is the natural operational state: a pool admin who deploys a curated pool and wants their allowlisted users to use the standard periphery router must add the router to the allowlist. The moment they do, the allowlist is silently open to all users. There is no warning in the extension or the router that this is the consequence. The likelihood is high for any curated pool that supports router-based swaps.

---

### Recommendation

The extension must check the economically relevant actor — the EOA initiating the transaction — not the intermediary. Two approaches:

1. **Pass the original initiator through the router.** Have `MetricOmmSimpleRouter` encode `msg.sender` (the EOA) in `extensionData` and have the extension decode and check it. This requires a convention between the router and the extension.

2. **Check `tx.origin` as a fallback.** If `sender` is a known router/contract, fall back to `tx.origin`. This is fragile and generally discouraged.

3. **Preferred: gate on `recipient` or require direct pool calls for curated pools.** Document that `SwapAllowlistExtension` only enforces correctly on direct `pool.swap()` calls, not router-mediated ones, and add a check in the extension that reverts if `sender` is not an EOA (i.e., `sender.code.length == 0`).

The cleanest fix is to have the router forward the original caller's address as part of `extensionData` and have `SwapAllowlistExtension` decode and check it when `sender` is a known intermediary.

---

### Proof of Concept

1. Deploy a pool with `SwapAllowlistExtension` configured.
2. Pool admin calls `swapExtension.setAllowedToSwap(pool, address(router), true)` to enable router-based swaps for allowlisted users.
3. Non-allowlisted EOA `attacker` calls `router.exactInputSingle({pool: pool, recipient: attacker, ...})`.
4. Router calls `pool.swap(attacker, ...)` with `msg.sender = router`.
5. Pool calls `_beforeSwap(router, ...)`.
6. `SwapAllowlistExtension.beforeSwap(sender=router, ...)` evaluates `allowedSwapper[pool][router]` → `true` → passes.
7. Swap executes; `attacker` receives tokens from the curated pool despite never being allowlisted.

The root cause is in `SwapAllowlistExtension.beforeSwap` checking `sender` (the router) instead of the originating EOA. [1](#0-0) [2](#0-1) [3](#0-2) [4](#0-3)

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
