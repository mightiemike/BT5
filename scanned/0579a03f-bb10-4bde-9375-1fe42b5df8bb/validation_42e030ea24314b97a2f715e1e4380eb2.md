### Title
`SwapAllowlistExtension` checks the immediate pool caller (`sender`) instead of the originating user, allowing any user to bypass the swap allowlist by routing through `MetricOmmSimpleRouter` — (`metric-periphery/contracts/extensions/SwapAllowlistExtension.sol`)

---

### Summary

`SwapAllowlistExtension.beforeSwap` gates swaps by checking `allowedSwapper[pool][sender]`, where `sender` is the address that called `pool.swap` — i.e., `msg.sender` of the pool. When a user routes through `MetricOmmSimpleRouter`, the pool's `msg.sender` is the router contract, not the originating user. If the pool admin allowlists the router (a natural step to enable router-based swaps), every user on the network can bypass the per-user allowlist by routing through the public router.

---

### Finding Description

**Step 1 — Pool passes `msg.sender` as `sender` to the extension.**

In `MetricOmmPool.swap`, the pool calls `_beforeSwap` with its own `msg.sender`: [1](#0-0) 

`ExtensionCalling._beforeSwap` then forwards that value verbatim as the first argument to every configured extension: [2](#0-1) 

**Step 2 — `SwapAllowlistExtension` keys the allowlist check on that `sender`.**

```solidity
// SwapAllowlistExtension.sol line 37
if (!allowAllSwappers[msg.sender] && !allowedSwapper[msg.sender][sender]) {
    revert IMetricOmmPoolActions.NotAllowedToSwap();
}
```

`msg.sender` here is the pool (correct). `sender` is whoever called `pool.swap` — the router, not the originating user. [3](#0-2) 

**Step 3 — `MetricOmmSimpleRouter` calls `pool.swap` directly, making itself the `sender`.**

For `exactInputSingle`: [4](#0-3) 

For every hop of `exactInput`: [5](#0-4) 

In both cases the pool receives `msg.sender = router`. The originating user's address is stored only in transient callback context for payment purposes and is never forwarded to the pool as `sender`.

**Step 4 — The bypass.**

A pool admin who wants to support router-based swaps must add the router to the allowlist:

```
extension.setAllowedToSwap(pool, address(router), true);
```

Once the router is allowlisted, `allowedSwapper[pool][router]` is `true` for every call that arrives through the router, regardless of who the originating user is. Any non-allowlisted address — including addresses the admin explicitly excluded — can call `router.exactInputSingle(pool, ...)` and the guard passes.

---

### Impact Explanation

The `SwapAllowlistExtension` is the production mechanism for curated pools that restrict trading to specific counterparties (e.g., whitelisted market makers, KYC'd participants, or protocol-controlled addresses). Bypassing it lets any public user execute swaps against a pool that was designed to be closed. The pool's LP assets are exposed to unrestricted arbitrage and directional flow from actors the admin explicitly intended to exclude. This is a direct loss of LP principal and fee revenue on every bypass swap.

---

### Likelihood Explanation

The bypass requires the pool admin to allowlist the router. This is a natural and expected operational step: any pool that wants to support the standard periphery UX must allowlist the router. The admin has no way to allowlist the router for specific users only — the router is a single address. The misconfiguration is therefore not an edge case; it is the only way to enable router-based swaps on an allowlisted pool.

---

### Recommendation

The extension must verify the originating user, not the immediate caller. Two approaches:

1. **Pass the originating user through the router.** The router should forward `msg.sender` as an explicit `originSender` field in `extensionData`, and the extension should decode and check that field instead of (or in addition to) `sender`.

2. **Check `sender` only when it is not a known router.** The extension could maintain a registry of trusted routers; when `sender` is a trusted router, it decodes the originating user from `extensionData` and checks that address instead.

Either way, the invariant must be: the address checked against the allowlist is the address that economically benefits from the swap (the originating user), not the address that mechanically called the pool.

---

### Proof of Concept

```
Setup:
  pool configured with SwapAllowlistExtension
  alice allowlisted: allowedSwapper[pool][alice] = true
  router allowlisted: allowedSwapper[pool][router] = true
    (admin adds router to support standard UX)

Attack:
  bob (not allowlisted) calls:
    router.exactInputSingle({pool: pool, recipient: bob, ...})

  router calls:
    pool.swap(bob, zeroForOne, amount, limit, "", extensionData)
    // msg.sender = router

  pool calls:
    _beforeSwap(router, bob, ...)

  extension checks:
    allowedSwapper[pool][router] == true  ✓  → swap proceeds

Result:
  bob executes a swap on a pool he was explicitly excluded from.
  The allowlist is fully bypassed for any user who routes through the router.
``` [3](#0-2) [1](#0-0) [4](#0-3)

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

**File:** metric-core/contracts/ExtensionCalling.sol (L160-177)
```text
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
