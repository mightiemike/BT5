### Title
SwapAllowlistExtension gates on the router's address instead of the actual user, allowing complete allowlist bypass via MetricOmmSimpleRouter — (File: metric-periphery/contracts/extensions/SwapAllowlistExtension.sol)

---

### Summary

`SwapAllowlistExtension.beforeSwap` checks the `sender` parameter against the per-pool allowlist. The pool passes `msg.sender` of its own `swap()` call as `sender`. When any swap is routed through `MetricOmmSimpleRouter`, `msg.sender` of the pool is the **router**, not the actual end user. If the router address is allowlisted for a pool, every user on the network can bypass the per-user allowlist by routing through the router.

---

### Finding Description

`MetricOmmPool.swap()` passes its own `msg.sender` as the `sender` argument to `_beforeSwap`, which forwards it verbatim to every configured extension:

```solidity
// MetricOmmPool.sol:230-240
_beforeSwap(
  msg.sender,   // ← immediate caller of the pool
  recipient,
  ...
);
```

`SwapAllowlistExtension.beforeSwap` then checks that value against the per-pool allowlist:

```solidity
// SwapAllowlistExtension.sol:31-41
function beforeSwap(address sender, address, bool, int128, uint128, uint256, uint128, uint128, bytes calldata)
  external view override returns (bytes4)
{
  if (!allowAllSwappers[msg.sender] && !allowedSwapper[msg.sender][sender]) {
    revert IMetricOmmPoolActions.NotAllowedToSwap();
  }
  return IMetricOmmExtensions.beforeSwap.selector;
}
```

Here `msg.sender` = pool, `sender` = immediate caller of the pool.

Every public entry point in `MetricOmmSimpleRouter` calls `pool.swap()` directly, making the router the pool's `msg.sender` for every hop:

```solidity
// MetricOmmSimpleRouter.sol:72-80  (exactInputSingle)
(int128 amount0Delta, int128 amount1Delta) = IMetricOmmPoolActions(params.pool)
  .swap(params.recipient, params.zeroForOne, ..., params.extensionData);

// MetricOmmSimpleRouter.sol:104-112  (exactInput, every hop)
(int128 amount0Delta, int128 amount1Delta) = IMetricOmmPoolActions(pool)
  .swap(i == last ? params.recipient : address(this), zeroForOne, ...);
```

The same holds for `exactOutputSingle` and `exactOutput` (including the recursive callback hops in `_exactOutputIterateCallback`). In every case the pool sees `msg.sender = router`, so the extension evaluates `allowedSwapper[pool][router]`, not `allowedSwapper[pool][actual_user]`.

**Concrete bypass path:**

A pool admin deploys a pool with `SwapAllowlistExtension` to restrict trading to a curated set of counterparties. To let those counterparties use the standard router UI, the admin calls `setAllowedToSwap(pool, router, true)`. From that moment, `allowedSwapper[pool][router] = true`, and the check in `beforeSwap` passes for **any** `sender` equal to the router — i.e., for every user who calls any of the four router entry points. The per-user allowlist is completely inoperative for router-mediated swaps.

---

### Impact Explanation

A pool protected by `SwapAllowlistExtension` is intended to be a permissioned venue — only allowlisted counterparties should be able to execute swaps. Once the router is allowlisted (a natural operational step for any pool that wants to support the standard UI), the gate is open to the entire public. Any user can drain liquidity from the pool at oracle-quoted prices, bypassing the access control that the pool admin believed was in place. This constitutes broken core pool functionality with direct loss of LP assets: LPs deposited into a pool they believed was restricted; unauthorized traders can now extract value from it at will.

---

### Likelihood Explanation

The trigger requires only that the pool admin allowlists the router — a routine, expected action for any pool that wants to support the standard periphery. No privileged attacker capability, no malicious token, no special timing. Any ordinary user who discovers the router is allowlisted can exploit it immediately. The misconfiguration is invisible from the admin's perspective because `isAllowedToSwap(pool, router)` returns `true` as intended, and there is no on-chain signal that this also opens the gate to all users.

---

### Recommendation

The extension must check the **original user**, not the immediate pool caller. Two complementary fixes:

1. **In the extension**: Gate on `sender` only when the pool is called directly. When the pool is called by a known router, the extension should require the router to forward the real user identity in `extensionData` and verify it there.

2. **Preferred — in the router**: Have the router encode the real `msg.sender` into `extensionData` for every swap call, and have `SwapAllowlistExtension.beforeSwap` decode and check that value when `sender` is a recognized router address.

3. **Simplest**: Document that allowlisting the router is equivalent to `setAllowAllSwappers(pool, true)` and remove the router from the allowlist model entirely, relying on a separate mechanism (e.g., a dedicated router-aware extension) for pools that need both router support and per-user gating.

---

### Proof of Concept

```
Setup
─────
1. Deploy pool with SwapAllowlistExtension configured on beforeSwap.
2. Pool admin calls setAllowedToSwap(pool, alice, true)       // alice is the intended user
3. Pool admin calls setAllowedToSwap(pool, router, true)      // enable router UI for alice

Attack
──────
4. Bob (not allowlisted) calls MetricOmmSimpleRouter.exactInputSingle({
       pool:      <restricted pool>,
       tokenIn:   token0,
       amountIn:  large,
       recipient: bob,
       ...
   })

5. Router calls pool.swap(bob, zeroForOne, ...) → pool's msg.sender = router
6. _beforeSwap(router, bob, ...) → extension checks allowedSwapper[pool][router] = true → passes
7. Swap executes; Bob receives token1 from the restricted pool.

Result: Bob, who was never allowlisted, successfully swaps against the pool.
        The per-user allowlist is completely bypassed.
```

**Key code references:**

- `sender` binding: [1](#0-0) 
- Extension check on `sender`: [2](#0-1) 
- Router always as pool's `msg.sender` (single-hop): [3](#0-2) 
- Router always as pool's `msg.sender` (multi-hop): [4](#0-3) 
- `_beforeSwap` forwarding `sender` verbatim: [5](#0-4)

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
