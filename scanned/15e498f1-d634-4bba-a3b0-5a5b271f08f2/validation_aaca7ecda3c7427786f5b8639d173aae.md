### Title
SwapAllowlistExtension Checks Router Address Instead of Actual User, Enabling Full Allowlist Bypass via MetricOmmSimpleRouter — (File: `metric-periphery/contracts/extensions/SwapAllowlistExtension.sol`)

---

### Summary

`SwapAllowlistExtension.beforeSwap` gates swaps by checking the `sender` argument, which the pool sets to `msg.sender` of the `pool.swap` call. When a user routes through `MetricOmmSimpleRouter`, `msg.sender` of `pool.swap` is the router contract, not the actual user. The extension therefore checks whether the **router** is allowlisted, not whether the **user** is allowlisted. If the pool admin allowlists the router to let permitted users access it, every unpermitted user gains the same access by routing through the same public contract.

---

### Finding Description

`MetricOmmPool.swap` passes `msg.sender` as the `sender` argument to `_beforeSwap`: [1](#0-0) 

`ExtensionCalling._beforeSwap` forwards that value unchanged to every configured extension: [2](#0-1) 

`SwapAllowlistExtension.beforeSwap` then checks `allowedSwapper[msg.sender][sender]`, where `msg.sender` is the pool and `sender` is whoever called `pool.swap`: [3](#0-2) 

`MetricOmmSimpleRouter.exactInputSingle` calls `pool.swap` directly, making the router the `msg.sender` of that call: [4](#0-3) 

The same pattern holds for `exactInput`, `exactOutputSingle`, and `exactOutput`. In every case the pool sees `msg.sender = router`, so the extension evaluates `allowedSwapper[pool][router]`, not `allowedSwapper[pool][actual_user]`.

The pool admin faces an inescapable dilemma:

- **Router NOT allowlisted**: every permitted user who tries to swap through the router is blocked, breaking the supported periphery path.
- **Router allowlisted**: the check collapses to a single bit for the entire router contract; any unpermitted user can call `router.exactInputSingle` and the extension passes them through.

There is no configuration that simultaneously allows permitted users to use the router and blocks unpermitted users from doing the same.

---

### Impact Explanation

A pool deploying `SwapAllowlistExtension` to restrict swaps to a curated set of counterparties (e.g., KYC-gated market makers, institutional LPs, or protocol-controlled addresses) loses that restriction entirely for any user who routes through `MetricOmmSimpleRouter`. Unpermitted users can execute swaps at oracle-derived prices against LP capital that was deposited under the assumption that only vetted counterparties would trade. This constitutes broken core pool functionality and a direct policy bypass with fund-impacting consequences for LPs on curated pools.

---

### Likelihood Explanation

`MetricOmmSimpleRouter` is the primary public swap entrypoint documented in the periphery. Any pool admin who allowlists the router to give permitted users access to it simultaneously opens the gate to all users. The attack requires no special privilege, no flash loan, and no unusual token behavior — a single public router call suffices.

---

### Recommendation

The `sender` argument passed to `beforeSwap` must represent the economically relevant actor, not the immediate `msg.sender` of `pool.swap`. Two sound approaches:

1. **Pass the originating user through `extensionData`**: have the router encode `msg.sender` into `extensionData` and have `SwapAllowlistExtension` decode and verify it. This requires the extension to trust that the pool's `msg.sender` is a known, non-spoofable router.
2. **Check `msg.sender` of the extension call against a router registry, then verify the user from `extensionData`**: the extension accepts the router as a valid caller only when the encoded user is individually allowlisted.

The simplest safe fix is to require that any intermediary (router) is itself responsible for forwarding the real user identity in `extensionData`, and that the extension validates both the intermediary and the forwarded identity.

---

### Proof of Concept

```
Setup
─────
1. Deploy pool with SwapAllowlistExtension as beforeSwap hook.
2. Admin calls setAllowedToSwap(pool, alice, true)   // alice is the permitted user
3. Admin calls setAllowedToSwap(pool, router, true)  // needed so alice can use the router

Attack
──────
4. bob (not allowlisted) calls:
       router.exactInputSingle({pool: pool, recipient: bob, ...})

5. Router executes:
       pool.swap(bob, zeroForOne, amount, priceLimit, "", extensionData)
       // msg.sender of pool.swap = router

6. Pool calls:
       _beforeSwap(sender=router, ...)

7. SwapAllowlistExtension evaluates:
       allowedSwapper[pool][router]  →  true   ✓

8. Bob's swap executes at oracle price against LP capital.
   The allowlist that was supposed to block bob is silently bypassed.

Invariant broken
────────────────
allowedSwapper[pool][bob] == false, yet bob's swap settles successfully.
```

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
