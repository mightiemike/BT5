### Title
SwapAllowlistExtension Gates the Router Address Instead of the Originating User, Allowing Any Caller to Bypass the Swap Allowlist via MetricOmmSimpleRouter — (`metric-periphery/contracts/extensions/SwapAllowlistExtension.sol`)

---

### Summary

`SwapAllowlistExtension.beforeSwap` checks `allowedSwapper[pool][sender]` where `sender` is the immediate `msg.sender` of `pool.swap()`. When a user routes through `MetricOmmSimpleRouter`, that `msg.sender` is the router contract, not the originating user. A pool admin who allowlists the router to enable router-mediated swaps inadvertently opens the gate to every user on the network, defeating the entire purpose of the allowlist.

---

### Finding Description

**Call path producing the wrong identity:**

```
User → MetricOmmSimpleRouter.exactInputSingle()
         → pool.swap(recipient, zeroForOne, amount, priceLimit, "", extensionData)
              msg.sender at pool = router address
         → MetricOmmPool._beforeSwap(msg.sender=router, recipient, ...)
         → SwapAllowlistExtension.beforeSwap(sender=router, ...)
              checks: allowedSwapper[pool][router]
```

In `MetricOmmPool.swap`, the pool unconditionally passes `msg.sender` as the `sender` argument to every extension:

```solidity
// metric-core/contracts/MetricOmmPool.sol line 230-240
_beforeSwap(
    msg.sender,   // ← always the immediate caller, i.e. the router
    recipient,
    ...
);
```

`SwapAllowlistExtension.beforeSwap` then evaluates:

```solidity
// metric-periphery/contracts/extensions/SwapAllowlistExtension.sol line 37-39
if (!allowAllSwappers[msg.sender] && !allowedSwapper[msg.sender][sender]) {
    revert IMetricOmmPoolActions.NotAllowedToSwap();
}
```

`msg.sender` here is the pool; `sender` is the router. The check resolves to `allowedSwapper[pool][router]`.

**The bypass:** A pool admin who wants their allowlisted users to be able to use the router (for slippage protection, multi-hop, deadline enforcement) must add the router to the allowlist. The moment `allowedSwapper[pool][router] = true`, every address on the network can call `MetricOmmSimpleRouter.exactInputSingle / exactInput / exactOutputSingle / exactOutput` and the extension check passes unconditionally — the originating user's address is never inspected.

There is no mechanism in the router to forward the original caller's identity to the extension. The router passes `params.extensionData` verbatim, but `SwapAllowlistExtension` never reads `extensionData`; it only reads `sender`.

---

### Impact Explanation

The `SwapAllowlistExtension` is the sole on-chain mechanism for a pool admin to restrict which addresses may trade against the pool's LP liquidity. Bypassing it means:

- Unauthorized addresses can execute swaps against a pool that was configured to be permissioned (e.g., an institutional pool, a pool with a specific counterparty whitelist, or a pool in a controlled launch phase).
- Every swap that should have been blocked can drain token0 or token1 from LP positions at the oracle-quoted price, causing direct loss of LP principal.
- Because the bypass is unconditional once the router is allowlisted, the entire allowlist invariant collapses for all router-mediated volume — which is the dominant interaction path for end users.

**Severity: High** — direct loss of LP principal through unauthorized swaps; the allowlist guard is completely ineffective for the router path.

---

### Likelihood Explanation

- The router is the standard, documented user-facing entry point for swaps. Any pool admin who deploys a permissioned pool and also wants their allowlisted users to benefit from slippage protection or multi-hop routing must allowlist the router.
- The admin has no alternative: there is no way to selectively allow specific users through the router without allowlisting the router itself.
- The bypass requires no special privileges, no flash loans, and no oracle manipulation — any EOA can call the router.
- **Likelihood: High.**

---

### Recommendation

The extension must verify the originating user, not the intermediate contract. Two viable approaches:

1. **Encode the real sender in `extensionData`:** Have the router encode `msg.sender` (the originating user) into the `extensionData` it forwards to the pool. `SwapAllowlistExtension.beforeSwap` decodes and checks that address instead of (or in addition to) `sender`. This requires a coordinated change in the router and the extension.

2. **Check `sender` AND require `sender != router`:** Reject any swap where `sender` is a known router unless the originating user is also encoded and verified. This is a defense-in-depth measure but still requires the extension to receive the real user identity.

3. **Document the limitation clearly:** If the design intent is that the allowlist only applies to direct pool callers, document that router-mediated swaps are always permitted and rename the extension accordingly. This is the least-change option but does not fix the security gap.

---

### Proof of Concept

```
Setup:
  pool = MetricOmmPool with SwapAllowlistExtension as beforeSwap hook
  admin calls: setAllowedToSwap(pool, userA, true)
    → allowedSwapper[pool][userA] = true
  admin calls: setAllowedToSwap(pool, router, true)
    → allowedSwapper[pool][router] = true
    (required so that userA can use the router)

Attack:
  attacker (not in allowlist) calls:
    MetricOmmSimpleRouter.exactInputSingle({
        pool: pool,
        recipient: attacker,
        zeroForOne: true,
        amountIn: X,
        ...
    })

  Router → pool.swap(attacker, true, X, ..., extensionData)
    pool._beforeSwap(msg.sender=router, ...)
      SwapAllowlistExtension.beforeSwap(sender=router, ...)
        allowedSwapper[pool][router] == true  ✓  → no revert

  Result: attacker's swap executes at the oracle price,
          draining token1 from LP positions.
          The allowlist provided zero protection.
```

**Relevant code locations:** [1](#0-0) [2](#0-1) [3](#0-2) [4](#0-3)

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
