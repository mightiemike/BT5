### Title
SwapAllowlistExtension Gates the Router Address Instead of the End User, Allowing Any User to Bypass a Curated Pool's Swap Allowlist via the Public Router - (File: `metric-periphery/contracts/extensions/SwapAllowlistExtension.sol`)

---

### Summary

`SwapAllowlistExtension.beforeSwap` checks the `sender` argument, which the pool sets to `msg.sender` of the `pool.swap()` call. When a user routes through `MetricOmmSimpleRouter`, `msg.sender` at the pool is the **router contract**, not the end user. A pool admin who allowlists the router (to enable standard periphery usage) inadvertently opens the pool to every user, completely defeating the per-user access control the allowlist was designed to enforce.

---

### Finding Description

`MetricOmmPool.swap` passes `msg.sender` as the `sender` argument to `_beforeSwap`: [1](#0-0) 

`ExtensionCalling._beforeSwap` forwards that value unchanged to every configured extension: [2](#0-1) 

`SwapAllowlistExtension.beforeSwap` then checks `allowedSwapper[msg.sender][sender]`, where `msg.sender` is the pool (used as the mapping key) and `sender` is the direct caller of `pool.swap()`: [3](#0-2) 

When a user calls `MetricOmmSimpleRouter.exactInputSingle`, the router calls `pool.swap()` directly: [4](#0-3) 

At that point `msg.sender` inside the pool is the **router address**, so the extension evaluates `allowedSwapper[pool][router]`, not `allowedSwapper[pool][end_user]`. The router carries no per-user identity into the allowlist check.

The same pattern applies to `exactInput`, `exactOutputSingle`, and `exactOutput`. [5](#0-4) 

---

### Impact Explanation

A pool admin who deploys a curated pool with `SwapAllowlistExtension` and allowlists the `MetricOmmSimpleRouter` (the natural step to let users access the pool through the standard periphery) inadvertently grants swap access to **every** address. Any non-allowlisted user can call `router.exactInputSingle` and the extension will pass because `allowedSwapper[pool][router] == true`. The per-user allowlist is completely bypassed, allowing unauthorized users to trade against the pool's liquidity. This is a direct policy-bypass with fund-impacting consequences: LP providers on a curated pool (e.g., one restricted to KYC'd counterparties or specific market makers) are exposed to trades from arbitrary actors they explicitly excluded.

---

### Likelihood Explanation

The `MetricOmmSimpleRouter` is the primary user-facing swap interface. Any pool admin who wants users to interact through the standard periphery must allowlist the router. The documentation and test suite present the router as the normal entry point. There is no warning that allowlisting the router collapses the per-user granularity of the allowlist. The misconfiguration is therefore the expected, natural configuration for any pool that intends to be accessible via the periphery. [6](#0-5) 

---

### Recommendation

The extension must resolve the **end user** identity, not the direct pool caller. Two sound approaches:

1. **Pass the original `msg.sender` through the router.** The router already knows the originating user (`msg.sender` at router entry). It can encode this in `extensionData` and the extension can decode and verify it — but this requires the extension to trust the router, which reintroduces an origin-check problem.

2. **Check `sender` only when the direct caller is not a known router; otherwise require the extension payload to carry a signed or router-attested user identity.** This is complex.

3. **Simplest correct fix:** The pool should pass the **original transaction initiator** (`tx.origin`) or the router should forward the user address as a verified first argument. Alternatively, the `SwapAllowlistExtension` should be documented as only suitable for pools where users call the pool directly (no router intermediary), and a separate `RouterSwapAllowlistExtension` should be built that decodes the user from `extensionData` with a router-signed attestation.

At minimum, the `SwapAllowlistExtension` NatSpec must warn that allowlisting any intermediary contract (router, aggregator, multicall) grants that contract's entire user base access to the pool.

---

### Proof of Concept

```
Setup:
  - Deploy pool with SwapAllowlistExtension
  - Admin calls setAllowedToSwap(pool, router, true)   // allow standard periphery
  - Admin does NOT call setAllowedToSwap(pool, attacker, true)

Attack:
  - attacker (not allowlisted) calls:
      router.exactInputSingle({pool: pool, ...})
  - router calls pool.swap(recipient, ...) with msg.sender = router
  - pool calls _beforeSwap(sender=router, ...)
  - SwapAllowlistExtension checks allowedSwapper[pool][router] == true  ✓
  - Swap executes successfully for the non-allowlisted attacker

Result:
  - attacker trades on a curated pool they were explicitly excluded from
  - LP providers are exposed to unauthorized counterparties
  - The allowlist invariant is broken for all router-mediated swaps
``` [7](#0-6) [8](#0-7)

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

**File:** metric-periphery/contracts/extensions/SwapAllowlistExtension.sol (L11-13)
```text
contract SwapAllowlistExtension is BaseMetricExtension, ISwapAllowlistExtension {
  mapping(address pool => mapping(address swapper => bool)) public allowedSwapper;
  mapping(address pool => bool) public allowAllSwappers;
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
