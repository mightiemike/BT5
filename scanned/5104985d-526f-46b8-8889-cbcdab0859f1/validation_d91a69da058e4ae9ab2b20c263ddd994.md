### Title
SwapAllowlistExtension Gates the Router Address Instead of the Real Swapper, Allowing Any User to Bypass a Curated Pool's Allowlist — (`metric-periphery/contracts/extensions/SwapAllowlistExtension.sol`)

---

### Summary

`SwapAllowlistExtension.beforeSwap` checks the `sender` argument forwarded by the pool, which equals the pool's `msg.sender`. When a user routes through `MetricOmmSimpleRouter`, the pool's `msg.sender` is the router contract, not the end user. The allowlist therefore gates the router's address. If the pool admin allowlists the router to enable router-mediated swaps, every unprivileged user can bypass the curated allowlist by routing through the router.

---

### Finding Description

`MetricOmmPool.swap` passes `msg.sender` as the `sender` argument to `_beforeSwap`: [1](#0-0) 

`ExtensionCalling._beforeSwap` forwards that value unchanged to every configured extension: [2](#0-1) 

`SwapAllowlistExtension.beforeSwap` then checks `allowedSwapper[pool][sender]`: [3](#0-2) 

When `MetricOmmSimpleRouter.exactInputSingle` (or `exactInput` / `exactOutput`) calls `pool.swap`, the pool's `msg.sender` is the router contract: [4](#0-3) 

The actual end user (`msg.sender` of the router call) is stored only in transient callback context for payment purposes and is never forwarded to the pool or to any extension. The extension therefore evaluates `allowedSwapper[pool][router]` — not `allowedSwapper[pool][user]`.

This creates an inescapable dilemma for the pool admin:

| Admin choice | Effect |
|---|---|
| Do **not** allowlist the router | Allowlisted users cannot use the router at all |
| **Allowlist the router** | Every user, including non-allowlisted ones, can bypass the gate by routing through the router |

---

### Impact Explanation

A curated pool whose entire purpose is to restrict swaps to a known set of counterparties loses that restriction entirely once the router is allowlisted. Any non-allowlisted user can execute swaps against the pool's LP assets, draining value from LPs who deposited under the assumption that only vetted counterparties could trade. This is a direct loss of LP principal and a complete failure of the pool's access-control invariant.

---

### Likelihood Explanation

The `MetricOmmSimpleRouter` is the canonical, publicly deployed periphery swap path. Any user who discovers the bypass can exploit it immediately with no special privileges, no malicious setup, and no non-standard tokens. The only precondition is that the pool admin has allowlisted the router (the natural action to take when deploying a curated pool that should still be usable via the standard router).

---

### Recommendation

The extension must receive the original end-user address, not the intermediary router address. Two viable approaches:

1. **Pass the real user through `extensionData`**: Define a convention where the router prepends the original `msg.sender` to `extensionData`, and the extension reads it from there (with the pool verifying the prepended address matches the callback payer stored in transient storage).

2. **Add a dedicated `swapOriginator` field to the pool's swap interface**: The pool passes both `msg.sender` (the direct caller) and an explicit originator address to extensions, with the router setting the originator to its own `msg.sender`.

Either approach must be validated so that a direct pool caller cannot spoof a different originator.

---

### Proof of Concept

1. Deploy a pool with `SwapAllowlistExtension` configured.
2. Call `setAllowedToSwap(pool, router, true)` — the natural step to enable router-mediated swaps.
3. As a non-allowlisted EOA `attacker`, call `MetricOmmSimpleRouter.exactInputSingle` targeting the pool.
4. The pool receives `msg.sender = router`; the extension checks `allowedSwapper[pool][router] == true` and passes.
5. The attacker's swap executes against the curated pool's LP assets despite never being allowlisted.

The check that should have blocked the attacker — `allowedSwapper[pool][attacker]` — is never evaluated. [5](#0-4) [6](#0-5)

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

**File:** metric-periphery/contracts/MetricOmmSimpleRouter.sol (L71-86)
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
    int128 out = MetricOmmSwapResults.extractAmountOut(params.zeroForOne, amount0Delta, amount1Delta);
    amountOut = MetricOmmSwapInputs.int128ToUint128(out);
    if (amountOut < params.amountOutMinimum) revert InsufficientOutput(amountOut, params.amountOutMinimum);

    _clearExpectedCallbackPool();
  }
```
