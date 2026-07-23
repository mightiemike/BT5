### Title
SwapAllowlistExtension Checks Router Address Instead of End User, Enabling Allowlist Bypass via MetricOmmSimpleRouter — (`metric-periphery/contracts/extensions/SwapAllowlistExtension.sol`)

---

### Summary

`SwapAllowlistExtension.beforeSwap` gates swaps by checking the `sender` argument, which is bound to `msg.sender` of `pool.swap()`. When `MetricOmmSimpleRouter` intermediates the call, `sender` is the router address, not the originating EOA. A pool admin who allowlists the router to enable router-based swaps for their curated pool inadvertently opens the pool to every user who can call the router, completely defeating the per-user allowlist.

---

### Finding Description

**Actor binding in the extension:**

`SwapAllowlistExtension.beforeSwap` receives `sender` as its first argument and checks it against the per-pool allowlist:

```solidity
function beforeSwap(address sender, address, bool, int128, uint128, uint256, uint128, uint128, bytes calldata)
    external view override returns (bytes4) {
    if (!allowAllSwappers[msg.sender] && !allowedSwapper[msg.sender][sender]) {
        revert IMetricOmmPoolActions.NotAllowedToSwap();
    }
    return IMetricOmmExtensions.beforeSwap.selector;
}
``` [1](#0-0) 

**What `sender` actually is:**

`MetricOmmPool.swap` passes `msg.sender` as the `sender` argument to `_beforeSwap`:

```solidity
_beforeSwap(
    msg.sender,   // <-- whoever called pool.swap()
    recipient,
    ...
);
``` [2](#0-1) 

**What the router passes:**

`MetricOmmSimpleRouter.exactInputSingle` calls `pool.swap()` directly. The router is `msg.sender` to the pool:

```solidity
(int128 amount0Delta, int128 amount1Delta) = IMetricOmmPoolActions(params.pool)
    .swap(
        params.recipient,
        params.zeroForOne,
        MetricOmmSwapInputs.asAmountSpecifiedIn(params.amountIn),
        priceLimitX64,
        "",
        params.extensionData
    );
``` [3](#0-2) 

The original EOA's address is stored only in transient storage for the payment callback (`_setNextCallbackContext`), and is never forwarded to the pool or the extension. The extension therefore evaluates `allowedSwapper[pool][router]`, not `allowedSwapper[pool][EOA]`.

**The dilemma this creates for pool admins:**

| Admin action | Effect |
|---|---|
| Does **not** allowlist the router | Allowlisted EOAs cannot use the router; they must call the pool directly |
| **Does** allowlist the router | Every EOA that can call the router bypasses the per-user allowlist |

A pool admin who wants to support the standard periphery path will allowlist the router. At that point the allowlist provides zero per-user gating: any non-allowlisted user routes through `MetricOmmSimpleRouter` and the extension passes because `allowedSwapper[pool][router] == true`.

The same structural mismatch applies to `exactInput` and `exactOutput` multi-hop paths, where the router is also `msg.sender` to every intermediate pool. [4](#0-3) 

---

### Impact Explanation

A curated pool deploying `SwapAllowlistExtension` to restrict trading to a specific set of counterparties (e.g., KYC-verified addresses, institutional partners) loses that restriction entirely once the router is allowlisted. Any unprivileged user can execute swaps at the oracle mid-price, draining LP value at the configured spread, with no recourse until the admin removes the router from the allowlist. This is a direct loss of LP principal on pools whose economic model depends on counterparty curation.

---

### Likelihood Explanation

`MetricOmmSimpleRouter` is the canonical production swap entry point documented in the periphery README. Pool admins operating curated pools will routinely allowlist it to avoid breaking the standard user experience. The mismatch between the admin's intent (allow the router as a trusted intermediary for allowlisted users) and the actual effect (allow all users) is non-obvious from the extension's interface. The trigger is a single `setAllowedToSwap(pool, router, true)` call by the pool admin, which is a normal operational action.

---

### Recommendation

The extension must gate on the economically relevant actor — the originating user — not the immediate caller of the pool. Two sound approaches:

1. **Pass the original sender explicitly**: Extend the pool's `swap` signature or the extension interface to carry the original `tx.origin`-equivalent (e.g., a user-supplied `onBehalfOf` address validated by the router via a signed permit), and check that address in the extension.
2. **Check `tx.origin` as a fallback**: For the allowlist extension specifically, check `tx.origin` when `msg.sender` (the pool) is being called by a known trusted router, so the extension sees the EOA that initiated the transaction.
3. **Router-level enforcement**: Have the router verify allowlist membership before calling the pool, and have the extension trust only direct pool calls (rejecting router-mediated calls unless the router attests the user).

---

### Proof of Concept

```
1. Deploy pool with SwapAllowlistExtension (extension1 = swapExt, beforeSwap order = 1).
2. Pool admin: swapExt.setAllowedToSwap(pool, alice, true)
              // alice is the only intended swapper
3. Pool admin: swapExt.setAllowedToSwap(pool, address(router), true)
              // admin allowlists the router so alice can use it
4. Bob (not allowlisted) calls:
       router.exactInputSingle(ExactInputSingleParams{
           pool: pool, recipient: bob, zeroForOne: true,
           amountIn: X, amountOutMinimum: 0, ...
       })
5. pool.swap() is called with msg.sender = router.
6. _beforeSwap passes sender = router to SwapAllowlistExtension.
7. Extension checks allowedSwapper[pool][router] == true → passes.
8. Bob's swap executes at oracle price, extracting LP value.
   alice's per-user allowlist entry is irrelevant.
```

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

**File:** metric-periphery/contracts/MetricOmmSimpleRouter.sol (L99-125)
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

      int128 amountInActual = MetricOmmSwapResults.extractAmountIn(zeroForOne, amount0Delta, amount1Delta);
      if (amountInActual < amount) revert InvalidInputAmountAtHop(uint8(i), amountInActual, amount);

      amount = MetricOmmSwapResults.extractAmountOut(zeroForOne, amount0Delta, amount1Delta);
    }

    if (amount <= 0) revert InvalidSwapDeltas();
    amountOut = MetricOmmSwapInputs.int128ToUint128(amount);
    if (amountOut < params.amountOutMinimum) revert InsufficientOutput(amountOut, params.amountOutMinimum);

    _clearExpectedCallbackPool();
  }
```
