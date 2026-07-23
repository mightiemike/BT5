### Title
`SwapAllowlistExtension` Gates the Router Address Instead of the Originating User, Allowing Any Caller to Bypass the Per-User Allowlist via the Router — (`metric-periphery/contracts/extensions/SwapAllowlistExtension.sol`)

---

### Summary

`SwapAllowlistExtension.beforeSwap` checks `sender`, which is `msg.sender` of `pool.swap()`. When a user routes through `MetricOmmSimpleRouter`, `msg.sender` of `pool.swap()` is the router contract, not the originating user. A pool admin who allowlists the router (the only way to let allowlisted users trade through the router) simultaneously opens the pool to every user on-chain, defeating the per-user access gate entirely.

---

### Finding Description

`MetricOmmPool.swap` passes its own `msg.sender` as the `sender` argument to `_beforeSwap`: [1](#0-0) 

`ExtensionCalling._beforeSwap` forwards that value unchanged to every configured extension: [2](#0-1) 

`SwapAllowlistExtension.beforeSwap` then checks `allowedSwapper[msg.sender][sender]`, where `msg.sender` is the pool and `sender` is whoever called `pool.swap()`: [3](#0-2) 

When a user calls `MetricOmmSimpleRouter.exactInputSingle`, the router is the entity that calls `pool.swap()`: [4](#0-3) 

So the allowlist check becomes `allowedSwapper[pool][router]`, not `allowedSwapper[pool][user]`.

This creates an irreconcilable inconsistency:

| Path | Identity checked |
|---|---|
| User → `pool.swap()` directly | `user` |
| User → `router.exactInputSingle()` → `pool.swap()` | `router` |

A pool admin who wants allowlisted users to be able to trade through the router **must** call `setAllowedToSwap(pool, router, true)`. The moment they do, the allowlist check for every router-mediated swap becomes `allowedSwapper[pool][router] == true`, which passes for **any** originating user. The per-user gate is completely bypassed for the router path.

The same structural problem exists for multi-hop `exactInput` (intermediate hops use `address(this)` as payer, but the pool still sees the router as `msg.sender`) and `exactOutput` (recursive callback path also calls `pool.swap()` from the router context). [5](#0-4) 

---

### Impact Explanation

A restricted pool using `SwapAllowlistExtension` is designed to limit swapping to a curated set of counterparties (e.g., institutional participants, KYC'd addresses). If the router is allowlisted — which is the only way to let those curated users trade through the standard periphery — any unpermissioned address can route through `MetricOmmSimpleRouter` and execute swaps against the pool's LP positions. This exposes LP capital to adversarial flow from actors the pool admin explicitly intended to exclude, constituting a direct loss path for LP principal.

---

### Likelihood Explanation

The trigger requires the pool admin to have allowlisted the router. This is the natural and expected configuration for any pool that wants its allowlisted users to access the standard periphery. The admin has no other option: either allowlist the router (opening the bypass) or leave it un-allowlisted (breaking router access for legitimate users). The bypass is therefore reachable in every realistic deployment of a router-accessible allowlisted pool.

---

### Recommendation

The extension must gate on the **originating user**, not the immediate caller of `pool.swap()`. Two viable approaches:

1. **Check `recipient` instead of `sender`** — for single-hop swaps the recipient is the user's chosen destination, but this is not reliable for multi-hop flows where intermediate recipients are the router itself.

2. **Require the originating user identity in `extensionData`** — the router forwards `extensionData` to the pool unchanged; the extension can decode a user-supplied address from `extensionData` and verify it against the allowlist, with the router responsible for injecting `msg.sender` before forwarding. This requires a coordinated router+extension design.

The simplest correct fix is to remove the router from the allowlist model entirely and instead have the router inject the originating user's address into `extensionData`, which the extension then verifies.

---

### Proof of Concept

```
Setup:
  pool configured with SwapAllowlistExtension
  allowedSwapper[pool][alice] = true   // alice is the only permitted swapper
  allowedSwapper[pool][router] = true  // admin adds router so alice can use it

Attack:
  bob (not allowlisted) calls:
    router.exactInputSingle({pool: pool, recipient: bob, ...})

  router calls:
    pool.swap(bob, zeroForOne, amount, priceLimit, "", extensionData)
    // msg.sender of pool.swap() = router

  pool calls:
    extension.beforeSwap(sender=router, ...)
    // check: allowedSwapper[pool][router] == true  ✓ passes

  bob's swap executes against LP positions — allowlist fully bypassed.
```

The attack requires zero privileged access. Bob is an ordinary unpermissioned address. The only precondition is that the pool admin has allowlisted the router, which is the standard configuration for any pool that intends to support periphery-mediated trading.

### Citations

**File:** metric-core/contracts/MetricOmmPool.sol (L230-241)
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
