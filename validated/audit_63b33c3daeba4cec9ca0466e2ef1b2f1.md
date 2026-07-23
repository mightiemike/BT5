Audit Report

## Title
SwapAllowlistExtension checks router address instead of originating user, enabling full allowlist bypass via MetricOmmSimpleRouter — (File: metric-periphery/contracts/extensions/SwapAllowlistExtension.sol)

## Summary
`SwapAllowlistExtension.beforeSwap` gates swaps by checking the `sender` argument, which `MetricOmmPool.swap` sets to its own `msg.sender`. When a user routes through `MetricOmmSimpleRouter`, the router is `msg.sender` of `pool.swap()`, so the extension evaluates `allowedSwapper[pool][router]` rather than `allowedSwapper[pool][actual_user]`. Any pool admin who allowlists the router to enable routing for permitted users simultaneously grants unrestricted swap access to every unpermitted user who calls the same router.

## Finding Description
**Root cause — actor substitution at the pool boundary:**

`MetricOmmPool.swap` passes its own `msg.sender` as the `sender` argument to `_beforeSwap`:

```solidity
// metric-core/contracts/MetricOmmPool.sol L230-240
_beforeSwap(
  msg.sender,   // ← router address when called via router
  recipient,
  ...
);
```

`ExtensionCalling._beforeSwap` forwards that value unchanged to every configured extension:

```solidity
// metric-core/contracts/ExtensionCalling.sol L149-177
abi.encodeCall(IMetricOmmExtensions.beforeSwap, (sender, recipient, ...))
```

`SwapAllowlistExtension.beforeSwap` then checks:

```solidity
// metric-periphery/contracts/extensions/SwapAllowlistExtension.sol L37-39
if (!allowAllSwappers[msg.sender] && !allowedSwapper[msg.sender][sender]) {
  revert IMetricOmmPoolActions.NotAllowedToSwap();
}
```

where `msg.sender` is the pool and `sender` is the router — never the originating user.

`MetricOmmSimpleRouter.exactInputSingle` calls `pool.swap` directly without encoding the real caller anywhere the extension can read:

```solidity
// metric-periphery/contracts/MetricOmmSimpleRouter.sol L72-80
(int128 amount0Delta, int128 amount1Delta) = IMetricOmmPoolActions(params.pool)
  .swap(params.recipient, params.zeroForOne, ..., params.extensionData);
```

The same substitution occurs in `exactInput` (L104), `exactOutputSingle` (L136), `exactOutput` (L165), and the recursive callback path `_exactOutputIterateCallback` (L220).

**Why existing checks fail:** The extension has no mechanism to distinguish a direct swap from a router-mediated one, and no path through which the real user's address reaches it. The `extensionData` field is passed through but the router never populates it with the originating user.

**Exploit flow:**
1. Pool admin deploys pool with `SwapAllowlistExtension`.
2. Admin calls `setAllowedToSwap(pool, alice, true)` (KYC'd user).
3. Admin calls `setAllowedToSwap(pool, router, true)` to let alice use the router.
4. Bob (not allowlisted) calls `router.exactInputSingle({pool: pool, recipient: bob, ...})`.
5. Router calls `pool.swap(bob, ...)` — router is `msg.sender`.
6. Extension checks `allowedSwapper[pool][router] == true` → passes.
7. Bob receives output tokens; `allowedSwapper[pool][bob]` was never evaluated.

## Impact Explanation
A pool protected by `SwapAllowlistExtension` for KYC, institutional, or regulatory access control is fully bypassed by any unpermitted user routing through `MetricOmmSimpleRouter`. Once the router is allowlisted — a necessary step for any permitted user to benefit from routing — the allowlist provides zero protection against router-mediated swaps. Non-permitted users can trade against the pool at oracle-quoted prices, causing direct loss of LP principal and defeating the pool's access-control invariant. This constitutes broken core pool functionality causing loss of funds and an admin-boundary break where the allowlist restriction is bypassed by an unprivileged path.

## Likelihood Explanation
The trigger is a routine, well-motivated admin action: allowlisting the router so that permitted users can access multi-hop, exact-output, and slippage-protected routing. The admin has no indication from the extension's interface, NatSpec, or documentation that doing so opens the pool to all users. The attack requires no special capability beyond calling a public router function. It is repeatable on every swap and affects every pool that uses this extension with the router allowlisted.

## Recommendation
The extension must gate the **originating user**, not the intermediary contract. Viable approaches:

1. **Router encodes real user in `extensionData`**: For every hop, the router encodes `msg.sender` (the actual user) into `extensionData`. The extension decodes and checks that address when `sender` is a recognized router address.
2. **Trusted-router registry in the extension**: Maintain a `trustedRouter` set; when `sender` is a trusted router, decode the real user from `extensionData` and apply `allowedSwapper` to that address.
3. **Check `recipient` instead of `sender`**: Only viable if the pool admin's intent is to gate who receives output rather than who initiates the trade; does not address initiation-side bypass.

## Proof of Concept
```
Setup:
  pool configured with SwapAllowlistExtension
  pool admin: setAllowedToSwap(pool, alice, true)   // alice is KYC'd
  pool admin: setAllowedToSwap(pool, router, true)  // to let alice use the router

Attack:
  bob (not allowlisted) calls router.exactInputSingle({pool: pool, recipient: bob, ...})
  → router calls pool.swap(recipient=bob, ...)        [msg.sender = router]
  → pool calls _beforeSwap(sender=router, ...)
  → extension checks allowedSwapper[pool][router] == true  ✓
  → swap executes; bob receives output tokens

Result:
  allowedSwapper[pool][bob] was never evaluated.
  Bob bypasses the allowlist and trades on a pool he is not permitted to access.
```

Foundry test outline: deploy pool + `SwapAllowlistExtension`, allowlist alice and router, call `router.exactInputSingle` from bob's address, assert the swap succeeds and bob receives tokens despite `allowedSwapper[pool][bob] == false`. [1](#0-0) [2](#0-1) [3](#0-2) [4](#0-3) [5](#0-4)

### Citations

**File:** metric-periphery/contracts/extensions/SwapAllowlistExtension.sol (L37-39)
```text
    if (!allowAllSwappers[msg.sender] && !allowedSwapper[msg.sender][sender]) {
      revert IMetricOmmPoolActions.NotAllowedToSwap();
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

**File:** metric-periphery/contracts/MetricOmmSimpleRouter.sol (L72-80)
```text
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
