### Title
SwapAllowlistExtension Gates the Router Address Instead of the End-User, Allowing Any User to Bypass the Swap Allowlist via MetricOmmSimpleRouter — (`metric-periphery/contracts/extensions/SwapAllowlistExtension.sol`)

---

### Summary

`SwapAllowlistExtension.beforeSwap` checks the `sender` argument against the per-pool allowlist. Because the pool always passes `msg.sender` of its own `swap` call as `sender`, and `MetricOmmSimpleRouter` is the immediate caller of `pool.swap`, the extension checks the **router's address** rather than the **end-user's address**. Any pool admin who allowlists the router (to let legitimate users trade through it) simultaneously opens the pool to every user on the network.

---

### Finding Description

**`SwapAllowlistExtension.beforeSwap` identity check:** [1](#0-0) 

```solidity
function beforeSwap(address sender, address, bool, int128, uint128, uint256, uint128, uint128, bytes calldata)
    external view override returns (bytes4)
{
    if (!allowAllSwappers[msg.sender] && !allowedSwapper[msg.sender][sender]) {
        revert IMetricOmmPoolActions.NotAllowedToSwap();
    }
    return IMetricOmmExtensions.beforeSwap.selector;
}
```

- `msg.sender` = the pool (correct; `onlyPool` is inherited from `BaseMetricExtension`)
- `sender` = the argument the pool forwarded

**What the pool forwards as `sender`:** [2](#0-1) 

```solidity
_beforeSwap(
    msg.sender,   // ← always the immediate caller of pool.swap()
    recipient,
    ...
);
```

**What the router passes as the immediate caller:** [3](#0-2) 

```solidity
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

The router calls `pool.swap(...)` directly. The pool's `msg.sender` is therefore the **router contract address**, not the end-user. The extension receives `sender = address(router)` and checks `allowedSwapper[pool][router]`.

**The trap:** A pool admin who wants allowlisted users to be able to trade through the router must add the router to the allowlist. Once `allowedSwapper[pool][router] = true`, the check `allowedSwapper[msg.sender][sender]` passes for **every** call that arrives through the router — regardless of who the actual end-user is. The per-user allowlist is completely neutralized.

The same bypass applies to `exactInput` (multi-hop) and `exactOutputSingle`/`exactOutput`: [4](#0-3) 

---

### Impact Explanation

A pool configured with `SwapAllowlistExtension` is intended to restrict trading to a specific set of addresses (e.g., KYC-verified users, institutional counterparties, or protocol-controlled addresses). Once the pool admin allowlists the router to support normal UX, the allowlist guard is fully bypassed for every user who routes through `MetricOmmSimpleRouter`. Non-allowlisted users can execute arbitrary swaps, draining pool liquidity at oracle-derived prices. This is a direct loss of LP principal and a complete admin-boundary break: an unprivileged path (the public router) defeats the pool admin's configured access control.

---

### Likelihood Explanation

**High.** `MetricOmmSimpleRouter` is the primary user-facing swap interface for the protocol. Any pool admin who deploys a permissioned pool and wants their allowlisted users to have a normal trading experience will naturally allowlist the router. The attacker requires no special privilege — only knowledge of the router address and the pool address. The attack is a single `exactInputSingle` call.

---

### Recommendation

The extension must check the **originating user**, not the immediate pool caller. Two viable approaches:

1. **Pass originator through `extensionData`:** The router encodes `msg.sender` into `extensionData` before forwarding to the pool. The extension decodes and checks it. This requires trusting the router to be honest, which is acceptable if the router is a known, immutable contract.

2. **Check `sender` only for direct calls; require router to be excluded from the allowlist:** Document that the router must never be allowlisted; allowlisted users must call the pool directly. This breaks router UX for permissioned pools.

3. **Protocol-level fix:** Add an `originator` field to the `beforeSwap` hook signature so the pool can propagate the true initiator through the extension call chain.

---

### Proof of Concept

```
1. Pool admin deploys pool with SwapAllowlistExtension.
2. Pool admin calls setAllowedToSwap(pool, allowedUser, true).
3. Pool admin calls setAllowedToSwap(pool, address(router), true)
   — to let allowedUser trade through the router.
4. nonAllowedUser calls:
     router.exactInputSingle(ExactInputSingleParams{
         pool: pool,
         recipient: nonAllowedUser,
         ...
     });
5. router calls pool.swap(...) → pool.msg.sender = router.
6. pool calls _beforeSwap(sender=router, ...).
7. Extension checks allowedSwapper[pool][router] → true.
8. Swap executes. nonAllowedUser receives output tokens.
   The per-user allowlist was never consulted.
```

The corrupted value is `allowedSwapper[pool][sender]` where `sender` is the router address rather than the end-user address. The invariant "only allowlisted addresses may swap in this pool" is broken for every user who routes through `MetricOmmSimpleRouter`.

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
