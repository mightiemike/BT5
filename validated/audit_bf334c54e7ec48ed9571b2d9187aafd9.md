### Title
`SwapAllowlistExtension.beforeSwap` checks the router address instead of the end-user, enabling full allowlist bypass for any user routing through `MetricOmmSimpleRouter` — (`metric-periphery/contracts/extensions/SwapAllowlistExtension.sol`)

---

### Summary

`SwapAllowlistExtension` gates pool swaps by checking the `sender` argument forwarded by the pool. Because `MetricOmmPool.swap` always passes `msg.sender` as `sender`, and `MetricOmmSimpleRouter` is `msg.sender` when it routes a swap, the extension sees the router address — not the actual end-user. A pool admin who allowlists the router (the only way to let allowlisted users reach the pool through the router) simultaneously opens the gate to every user on the router, defeating the allowlist entirely.

---

### Finding Description

`MetricOmmPool.swap` forwards `msg.sender` as the `sender` argument to every before-swap hook: [1](#0-0) 

`ExtensionCalling._beforeSwap` encodes that value verbatim into the hook call: [2](#0-1) 

`SwapAllowlistExtension.beforeSwap` then checks `allowedSwapper[msg.sender][sender]`, where `msg.sender` is the pool and `sender` is the direct caller of `pool.swap`: [3](#0-2) 

When a user routes through `MetricOmmSimpleRouter.exactInputSingle` (or any other router entry point), the router is `msg.sender` of `pool.swap`: [4](#0-3) 

So the extension evaluates `allowedSwapper[pool][router]`, not `allowedSwapper[pool][actualUser]`.

This creates an irresolvable dilemma for the pool admin:

| Configuration | Effect |
|---|---|
| Router **not** allowlisted | Allowlisted users cannot use the router at all (broken functionality) |
| Router **allowlisted** | Every user on the router bypasses the allowlist (security failure) |

There is no configuration that simultaneously allows specific users to reach the pool through the router while blocking others. The identity the extension checks (the router address) is permanently misbound from the identity the pool admin intends to gate (the end-user address).

---

### Impact Explanation

A curated pool deploying `SwapAllowlistExtension` to restrict trading to a known set of counterparties loses that restriction entirely for any user who routes through `MetricOmmSimpleRouter`. Non-allowlisted users can execute swaps at oracle-derived prices, draining LP value or violating the pool's intended access policy. This is a direct loss of LP principal and a broken core pool invariant (the allowlist guard).

---

### Likelihood Explanation

The router is the primary user-facing swap entry point in the periphery. Any pool admin who wants allowlisted users to benefit from multi-hop routing, exact-output swaps, or deadline/slippage protection must allowlist the router. The moment they do, the bypass is live for all users. The trigger requires no privileged access, no special token behavior, and no unusual timing — any public user can call `exactInputSingle` on the router against the pool.

---

### Recommendation

The extension must gate the actual end-user, not the direct caller of `pool.swap`. Two viable approaches:

1. **Router-forwarded identity**: Have `MetricOmmSimpleRouter` encode the originating user address into `extensionData` for each hop, and have `SwapAllowlistExtension.beforeSwap` decode and check that address when `sender` is a known router. This requires a trusted router registry or a signed payload.

2. **Sender-only policy**: Document that `SwapAllowlistExtension` is incompatible with router-mediated swaps and enforce this at pool creation (e.g., factory-level validation that rejects pools combining this extension with a public router allowlist entry). Pool admins must allowlist individual user addresses and those users must call `pool.swap` directly.

---

### Proof of Concept

```
1. Pool admin deploys pool with SwapAllowlistExtension.
2. Pool admin calls setAllowedToSwap(pool, alice, true)   // alice is the intended gated user
3. Pool admin calls setAllowedToSwap(pool, router, true)  // necessary for alice to use the router
4. bob (not allowlisted) calls:
       router.exactInputSingle({pool: pool, ...})
5. Router calls pool.swap(recipient=bob, ...) with msg.sender = router.
6. _beforeSwap forwards sender = router to SwapAllowlistExtension.beforeSwap.
7. Extension evaluates: allowedSwapper[pool][router] == true  →  passes.
8. bob's swap executes at oracle price, bypassing the allowlist entirely.
```

The corrupted value is the identity checked by the guard: `router address` is substituted for `bob's address`, causing the allowlist to pass where it must block. [5](#0-4) [4](#0-3) [1](#0-0)

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
