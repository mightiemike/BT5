### Title
`SwapAllowlistExtension` gates the router address instead of the actual end-user, making the allowlist either a DoS for legitimate users or trivially bypassable — (`metric-periphery/contracts/extensions/SwapAllowlistExtension.sol`)

### Summary

`SwapAllowlistExtension.beforeSwap` checks `sender` — the direct caller of `MetricOmmPool.swap` — against the per-pool allowlist. When users route through `MetricOmmSimpleRouter`, `sender` is the router contract, not the actual user. This creates an irresolvable dilemma: either allowlisted users cannot use the router (DoS on the primary periphery), or the admin allowlists the router itself, which lets any unprivileged user bypass the guard entirely.

### Finding Description

`SwapAllowlistExtension.beforeSwap` receives `sender` as its first argument and checks it against `allowedSwapper[msg.sender][sender]`, where `msg.sender` is the pool: [1](#0-0) 

The pool's `ExtensionCalling._beforeSwap` forwards whatever address the pool received as `msg.sender` of the `swap` call as the `sender` argument: [2](#0-1) 

`MetricOmmSimpleRouter.exactInput` calls `pool.swap(...)` directly, making the pool's `msg.sender` the router contract, not the originating user: [3](#0-2) 

The same pattern applies to `exactInputSingle`, `exactOutput`, and `exactOutputSingle`. In every case the pool sees `msg.sender = router`, so `sender = router` is what the extension checks.

**Scenario A — DoS on allowlisted users:**
Admin calls `setAllowedToSwap(pool, alice, true)`. Alice calls `router.exactInputSingle(...)`. The router calls `pool.swap(...)`. The pool calls `extension.beforeSwap(router, ...)`. The extension checks `allowedSwapper[pool][router]` → `false` → `NotAllowedToSwap`. Alice's swap reverts despite being explicitly allowlisted.

**Scenario B — Full allowlist bypass:**
To fix Scenario A, the admin calls `setAllowedToSwap(pool, router, true)`. Now `allowedSwapper[pool][router] = true`. Any user — including Bob who was never allowlisted — calls `router.exactInputSingle(...)`. The router calls `pool.swap(...)`. The extension checks `allowedSwapper[pool][router]` → `true` → passes. Bob swaps freely in a pool that was supposed to be restricted.

The `DepositAllowlistExtension` does not share this flaw because it checks `owner` (the LP position owner, explicitly passed by the caller), not `sender`: [4](#0-3) 

### Impact Explanation

In Scenario B, any unprivileged user bypasses the pool admin's intended access control by routing through the public `MetricOmmSimpleRouter`. The allowlist — the only mechanism gating who may trade in a restricted pool — is rendered inoperative. This is an admin-boundary break: a factory-configured guard is bypassed by an unprivileged path (the public router). Pools deployed with `SwapAllowlistExtension` for regulatory, KYC, or LP-protection reasons silently accept trades from any address.

### Likelihood Explanation

The `MetricOmmSimpleRouter` is the primary user-facing swap interface. Any pool admin who enables the allowlist and also wants their allowlisted users to use the router will naturally allowlist the router address, unknowingly opening the gate to all users. The trigger requires no special privilege — any user with a token balance can call the router.

### Recommendation

Pass the originating user through the call chain rather than the direct pool caller. Two options:

1. **Preferred:** Add an `originator` field to `extensionData` that the router populates with `msg.sender` before forwarding to the pool. The extension reads and verifies this field (with the pool as the trusted source of the outer call).
2. **Alternative:** Change `beforeSwap` to check `recipient` (the address that receives output tokens) instead of `sender`, since the router always sets `recipient` to the actual user or a downstream address controlled by the user.

### Proof of Concept

```
1. Deploy pool with SwapAllowlistExtension.
2. Admin calls setAllowedToSwap(pool, alice, true).
3. Admin calls setAllowedToSwap(pool, router, true)   // to let alice use the router
4. Bob (never allowlisted) calls:
       router.exactInputSingle({pool: pool, tokenIn: T0, tokenOut: T1, ...})
5. Router calls pool.swap(bob_recipient, ...)
6. Pool calls extension.beforeSwap(router, ...)
7. Extension checks allowedSwapper[pool][router] == true  → passes
8. Bob's swap executes in the restricted pool.
``` [5](#0-4) [3](#0-2)

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

**File:** metric-periphery/contracts/extensions/DepositAllowlistExtension.sol (L32-42)
```text
  function beforeAddLiquidity(address, address owner, uint80, LiquidityDelta calldata, bytes calldata)
    external
    view
    override
    returns (bytes4)
  {
    if (!allowAllDepositors[msg.sender] && !allowedDepositor[msg.sender][owner]) {
      revert IMetricOmmPoolActions.NotAllowedToDeposit();
    }
    return IMetricOmmExtensions.beforeAddLiquidity.selector;
  }
```
