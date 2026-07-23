### Title
`SwapAllowlistExtension` Gates the Router Address Instead of the Originating User, Allowing Any Caller to Bypass the Swap Allowlist — (`metric-periphery/contracts/extensions/SwapAllowlistExtension.sol`)

### Summary

`SwapAllowlistExtension.beforeSwap` checks the `sender` argument forwarded by the pool, which is `msg.sender` of the pool's `swap()` call. When a user swaps through `MetricOmmSimpleRouter`, the pool's `msg.sender` is the router contract, not the originating user. If the router is added to the allowlist (the only way to enable router-based swaps on an allowlisted pool), every user—including those not on the allowlist—can bypass the guard by routing through the public router.

### Finding Description

The pool's `swap()` function passes its own `msg.sender` as the `sender` argument to the `beforeSwap` extension hook:

```solidity
// MetricOmmPool.sol:230-240
_beforeSwap(
  msg.sender,   // ← whoever called pool.swap()
  recipient,
  ...
);
```

`SwapAllowlistExtension.beforeSwap` then checks that `sender` against the per-pool allowlist:

```solidity
// SwapAllowlistExtension.sol:31-41
function beforeSwap(address sender, address, bool, int128, uint128, uint256, uint128, uint128, bytes calldata)
  external view override returns (bytes4)
{
  if (!allowAllSwappers[msg.sender] && !allowedSwapper[msg.sender][sender]) {
    revert IMetricOmmPoolActions.NotAllowedToSwap();
  }
  return IMetricOmmExtensions.beforeSwap.selector;
}
```

`MetricOmmSimpleRouter.exactInputSingle` calls `pool.swap()` directly, making the router the pool's `msg.sender`:

```solidity
// MetricOmmSimpleRouter.sol:72-80
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

The same pattern holds for `exactInput` (all hops), `exactOutputSingle`, and `exactOutput` (all hops including the recursive callback path at line 220-228). In every case the pool sees the router as `msg.sender`, so the extension checks `allowedSwapper[pool][router]`, not `allowedSwapper[pool][originalUser]`.

**Bypass path:**
1. Pool admin deploys a pool with `SwapAllowlistExtension` to restrict swaps to a set of KYC'd addresses.
2. To allow those addresses to use the standard router UI, the admin must add the router to the allowlist (`setAllowedToSwap(pool, router, true)`).
3. Once the router is allowlisted, any unprivileged user calls `router.exactInputSingle(...)`. The extension sees `sender = router`, which is allowlisted, and the guard passes unconditionally.
4. The original user's address is never checked.

### Impact Explanation

The swap allowlist guard is completely bypassed for any user who routes through the public `MetricOmmSimpleRouter`. Pools intended to be restricted (institutional, KYC-gated, or otherwise access-controlled) become open to arbitrary swappers. Unauthorized actors can execute swaps against the pool's liquidity, draining LP value or manipulating pool state in ways the pool admin explicitly intended to prevent. This is broken core pool functionality with direct LP asset exposure.

### Likelihood Explanation

The router is a public, permissionless contract. Any user who knows its address can call `exactInputSingle` or `exactInput`. The only precondition is that the pool admin has added the router to the allowlist—which is the natural and necessary step to enable the standard UI for allowlisted users. The bypass requires no special privileges, no flash loans, and no complex setup.

### Recommendation

The `SwapAllowlistExtension` should gate the **originating user**, not the intermediary. Two approaches:

1. **Pass the original user through `extensionData`**: The router encodes `msg.sender` into `extensionData`; the extension decodes and checks it. This requires the extension to trust the router's encoding, which reintroduces a trust assumption.

2. **Check `tx.origin` as a fallback** (not recommended for general use, but acceptable in restricted-pool contexts where the allowlist is the primary control).

3. **Preferred**: Require allowlisted users to call the pool directly (document that the router is incompatible with `SwapAllowlistExtension`), or introduce a dedicated allowlist-aware router that forwards the original caller identity in a verifiable way (e.g., signed permit or trusted forwarder pattern).

### Proof of Concept

```
Setup:
  - Deploy pool with SwapAllowlistExtension
  - allowedSwapper[pool][alice] = true   (alice is KYC'd)
  - allowedSwapper[pool][router] = true  (router added so alice can use UI)

Attack:
  - bob (not allowlisted) calls router.exactInputSingle({pool: pool, ...})
  - Router calls pool.swap(recipient, ...)
  - Pool calls _beforeSwap(msg.sender=router, ...)
  - Extension checks allowedSwapper[pool][router] → true → passes
  - Bob's swap executes against the pool's liquidity
  - Allowlist is bypassed; bob is never checked
``` [1](#0-0) [2](#0-1) [3](#0-2) [4](#0-3) [5](#0-4) [6](#0-5)

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

**File:** metric-periphery/contracts/MetricOmmSimpleRouter.sol (L220-228)
```text
    (int128 amount0DeltaReturned, int128 amount1DeltaReturned) = IMetricOmmPoolActions(pool)
      .swap(
        msg.sender,
        zeroForOne,
        MetricOmmSwapInputs.asAmountSpecifiedFromPositive(amountToPay),
        MetricOmmSwapPath.openLimit(zeroForOne),
        data,
        cb.extensionDatas[tradesLeft]
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
