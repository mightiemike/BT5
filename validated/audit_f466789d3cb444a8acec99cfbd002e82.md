### Title
`SwapAllowlistExtension.beforeSwap` checks the router address as the swapper identity, allowing any user to bypass the per-user swap allowlist via `MetricOmmSimpleRouter` — (`metric-periphery/contracts/extensions/SwapAllowlistExtension.sol`)

---

### Summary

`SwapAllowlistExtension` gates pool swaps by checking `allowedSwapper[pool][sender]`, where `sender` is the direct caller of `pool.swap()`. When a user routes through `MetricOmmSimpleRouter`, the router becomes the direct caller, so the extension checks `allowedSwapper[pool][router]` instead of `allowedSwapper[pool][user]`. If the pool admin allowlists the router address (a natural step to enable router-mediated swaps for their curated pool), every user — including those not individually allowlisted — can bypass the restriction by routing through the public router.

---

### Finding Description

`MetricOmmPool.swap()` passes `msg.sender` as the `sender` argument to `_beforeSwap`: [1](#0-0) 

`ExtensionCalling._beforeSwap` forwards that value unchanged to every configured extension: [2](#0-1) 

`SwapAllowlistExtension.beforeSwap` then checks the allowlist keyed on that `sender`: [3](#0-2) 

When a user calls `MetricOmmSimpleRouter.exactInputSingle`, the router calls `pool.swap()` directly: [4](#0-3) 

At that point `msg.sender` inside `pool.swap()` is the router contract, not the originating user. The extension therefore evaluates `allowedSwapper[pool][router]`, not `allowedSwapper[pool][user]`.

A pool admin who wants allowlisted users to be able to use the router must add the router to the allowlist. Once the router is allowlisted, the check `allowedSwapper[pool][router]` is `true` for every call that arrives through the router, regardless of who the originating user is. Any non-allowlisted user can then call `router.exactInputSingle(...)` and the extension passes unconditionally.

The pool admin cannot simultaneously achieve "only specific users may swap" and "those users may use the router" — allowlisting the router collapses the per-user gate entirely.

---

### Impact Explanation

A curated pool's swap allowlist is completely bypassed. Any address can trade on a pool that was intended to be restricted to a specific set of participants (e.g., KYC-verified users, whitelisted market makers). This breaks the core curation invariant, exposes LP funds to unrestricted arbitrage or manipulation from unauthorized actors, and can cause direct loss of LP principal through adverse price impact from trades the pool was designed to exclude.

---

### Likelihood Explanation

The bypass requires the pool admin to have allowlisted the router address. This is a natural and expected configuration step: without it, even allowlisted users cannot use the router. Any pool that intends to support both per-user curation and router-mediated swaps will reach this configuration. The router is a public, permissionless contract, so once the router is allowlisted the bypass is reachable by any address with no further preconditions.

---

### Recommendation

The extension must check the economically relevant actor — the address that initiated the transaction — not the direct caller of `pool.swap()`. Two complementary fixes:

1. **Pass the original user through the router.** The router already stores the original `msg.sender` in transient storage as the payer. The pool could accept an optional `originSender` hint in `extensionData`, or the extension interface could be extended with an `originSender` field.

2. **Check `tx.origin` as a fallback (not recommended alone).** `tx.origin` is the EOA that signed the transaction, but it breaks contract-wallet and smart-account flows.

The cleanest fix is to have the router encode the original `msg.sender` into `extensionData` and have `SwapAllowlistExtension` decode and check that value when the direct `sender` is a known router, or to redesign the hook interface to carry a separate `originSender` field that the pool populates from a verified source.

---

### Proof of Concept

```
Setup:
  pool configured with SwapAllowlistExtension
  pool admin calls setAllowedToSwap(pool, userA, true)   // allowlist userA
  pool admin calls setAllowedToSwap(pool, router, true)  // allowlist router so userA can use it

Attack:
  userB (not allowlisted) calls:
    router.exactInputSingle({pool: pool, tokenIn: ..., tokenOut: ..., ...})

  Execution trace:
    router.exactInputSingle()
      -> pool.swap(recipient, ..., sender=router)
        -> _beforeSwap(sender=router, ...)
          -> SwapAllowlistExtension.beforeSwap(sender=router, ...)
            -> allowedSwapper[pool][router] == true  ✓  (passes)
        -> swap executes normally

  Result: userB successfully swaps on a pool they are not allowlisted for.
``` [5](#0-4) [6](#0-5)

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
