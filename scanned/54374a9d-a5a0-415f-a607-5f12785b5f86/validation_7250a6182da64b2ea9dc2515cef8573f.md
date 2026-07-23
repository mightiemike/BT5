### Title
SwapAllowlistExtension Gates the Router Address Instead of the End User, Allowing Any User to Bypass the Swap Allowlist - (`metric-periphery/contracts/extensions/SwapAllowlistExtension.sol`)

---

### Summary

`SwapAllowlistExtension.beforeSwap` checks the `sender` argument passed by the pool, which is `msg.sender` of `pool.swap()`. When a user routes through `MetricOmmSimpleRouter`, `msg.sender` to the pool is the **router contract**, not the end user. The extension therefore checks whether the router is allowlisted, not whether the actual trader is allowlisted. If the pool admin adds the router to the allowlist (the only way to let legitimate users trade through the router), the allowlist is completely bypassed for every user on the network.

---

### Finding Description

**Pool's `swap()` passes `msg.sender` as `sender` to the extension:** [1](#0-0) 

`msg.sender` here is whoever called `pool.swap()`. When the call originates from `MetricOmmSimpleRouter.exactInputSingle`, `msg.sender` is the router contract address.

**`SwapAllowlistExtension.beforeSwap` checks that `sender` argument:** [2](#0-1) 

`msg.sender` inside the extension is the pool (enforced by `onlyPool`), and `sender` is the router address. The check `allowedSwapper[pool][router]` is evaluated — not `allowedSwapper[pool][end_user]`.

**Router calls `pool.swap()` directly, substituting itself as `msg.sender`:** [3](#0-2) 

The router stores the real payer in transient storage for the callback, but the pool's `swap()` call sees `msg.sender = router`. The end user's address is never forwarded to the extension.

**The same structural problem exists for `exactInput` (multi-hop) and `exactOutputSingle`:** [4](#0-3) 

Every hop calls `pool.swap()` from the router, so every hop's `sender` seen by the extension is the router address.

---

### Impact Explanation

A pool admin who deploys a `SwapAllowlistExtension`-gated pool faces an inescapable dilemma:

1. **Router not allowlisted**: Legitimate allowlisted users cannot trade through `MetricOmmSimpleRouter` at all — the extension reverts with `NotAllowedToSwap` because `allowedSwapper[pool][router] == false`. The standard periphery interface is broken for all allowlisted users.

2. **Router allowlisted** (the only fix): Any user on the network — including those explicitly excluded from the allowlist — can bypass the guard by calling `router.exactInputSingle()`. The allowlist provides zero protection.

In either case the allowlist invariant is broken. For pools designed for KYC compliance, institutional-only access, or any access-controlled trading, unauthorized users gain full swap access to pool liquidity, draining LP value at oracle-anchored prices with no recourse.

---

### Likelihood Explanation

- `MetricOmmSimpleRouter` is the standard, documented swap interface for end users.
- Pool admins who configure a `SwapAllowlistExtension` will naturally need to allowlist the router so their permitted users can trade — triggering the bypass.
- No special privileges, flash loans, or unusual conditions are required. Any EOA can call `router.exactInputSingle()`.
- The bypass is reachable on every pool that uses `SwapAllowlistExtension` with the router allowlisted.

---

### Recommendation

The extension must receive the **end user's address**, not the intermediary's address. Two approaches:

1. **Pass the original caller through `extensionData`**: The router encodes `msg.sender` into `extensionData`; the extension decodes and checks it. This requires router cooperation and is opt-in.

2. **Add a `msgSender` parameter to the extension interface**: The pool passes both `msg.sender` (the immediate caller) and an authenticated original-sender field. This requires a protocol-level interface change.

The cleanest fix is for the pool to pass the original user address as a separate authenticated field that extensions can rely on, rather than reusing `msg.sender` of `pool.swap()` as the identity to gate.

---

### Proof of Concept

```
Setup:
  - Deploy pool with SwapAllowlistExtension (beforeSwap order = extension 1)
  - allowedSwapper[pool][router] = true   (admin adds router so users can trade)
  - allowedSwapper[pool][alice] = true    (alice is a legitimate allowlisted user)
  - allowedSwapper[pool][bob]   = false   (bob is explicitly excluded)

Attack:
  1. bob calls router.exactInputSingle({pool: pool, ...})
  2. router calls pool.swap(recipient=bob, ...) — msg.sender to pool = router
  3. pool calls _beforeSwap(sender=router, ...)
  4. SwapAllowlistExtension checks allowedSwapper[pool][router] == true → passes
  5. bob's swap executes against pool liquidity

Result:
  - bob, who is explicitly excluded from the allowlist, completes a swap
  - The allowlist guard is fully bypassed via the public router
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
