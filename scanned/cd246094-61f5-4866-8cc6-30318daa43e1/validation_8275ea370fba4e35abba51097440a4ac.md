### Title
`SwapAllowlistExtension::beforeSwap` gates the router address instead of the end-user, making the per-user swap allowlist permanently bypassable through `MetricOmmSimpleRouter` — (`metric-periphery/contracts/extensions/SwapAllowlistExtension.sol`)

---

### Summary

`SwapAllowlistExtension.beforeSwap` checks `sender`, which is `msg.sender` of the pool's `swap()` call. When a user routes through `MetricOmmSimpleRouter`, `msg.sender` at the pool is the **router address**, not the actual user. The extension therefore gates the router's identity, not the end-user's identity. This produces two mutually exclusive failure modes: (a) allowlisted users cannot use the router at all, or (b) if the pool admin allowlists the router to restore router access, every non-allowlisted user can bypass the guard by routing through it.

---

### Finding Description

In `MetricOmmPool.swap`, the pool passes `msg.sender` as the `sender` argument to `_beforeSwap`:

```solidity
// MetricOmmPool.sol:230-240
_beforeSwap(
  msg.sender,   // ← router address when called via MetricOmmSimpleRouter
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

`_beforeSwap` forwards this value unchanged to every configured extension:

```solidity
// ExtensionCalling.sol:160-176
_callExtensionsInOrder(
  BEFORE_SWAP_ORDER,
  abi.encodeCall(
    IMetricOmmExtensions.beforeSwap,
    (sender, recipient, zeroForOne, amountSpecified, ...)
  )
);
```

`SwapAllowlistExtension.beforeSwap` then evaluates:

```solidity
// SwapAllowlistExtension.sol:37-39
if (!allowAllSwappers[msg.sender] && !allowedSwapper[msg.sender][sender]) {
  revert IMetricOmmPoolActions.NotAllowedToSwap();
}
```

Here `msg.sender` is the pool and `sender` is the router. The mapping lookup is `allowedSwapper[pool][router]`. The extension has no visibility into which end-user initiated the call through the router.

`MetricOmmSimpleRouter.exactInputSingle` (and `exactInput`, `exactOutputSingle`, `exactOutput`) all call `pool.swap()` directly with no mechanism to forward the originating user's address:

```solidity
// MetricOmmSimpleRouter.sol:72-80
(int128 amount0Delta, int128 amount1Delta) = IMetricOmmPoolActions(params.pool)
  .swap(
    params.recipient,
    params.zeroForOne,
    MetricOmmSwapInputs.asAmountSpecifiedIn(params.amountIn),
    priceLimitX64,
    "",
    params.extensionData   // extensionData is user-supplied; SwapAllowlistExtension ignores it
  );
```

The `extensionData` field is forwarded but `SwapAllowlistExtension` does not read it, so there is no existing path to convey user identity through the router.

**Failure mode A — allowlisted users locked out of the router:**  
Pool admin allowlists `userA` and `userB` by address. Both can swap directly (`pool.swap()` with `msg.sender = userA`). When either tries to swap via the router, `sender = router` → `allowedSwapper[pool][router]` is `false` → `NotAllowedToSwap`. The router is permanently unusable for any allowlisted user unless the router itself is allowlisted.

**Failure mode B — full allowlist bypass:**  
To restore router access, the pool admin allowlists the router address. Now `allowedSwapper[pool][router] = true`. Any user — including those the admin explicitly never allowlisted — can call `router.exactInputSingle(...)` and the extension passes unconditionally, because the check resolves to `allowedSwapper[pool][router]` regardless of who the actual caller is.

---

### Impact Explanation

The `SwapAllowlistExtension` is the protocol's only per-pool swap access-control primitive. Its structural inability to distinguish end-users through the router means:

- In failure mode A, allowlisted users are forced to interact directly with the pool contract, bypassing slippage protection, multi-hop routing, and deadline enforcement provided by the router — a broken core swap flow.
- In failure mode B, the guard is fully neutralised: any address can swap in a pool the admin intended to restrict. If the allowlist was deployed to exclude adversarial or non-KYC'd traders, those traders can now drain LP value through informed swaps, directly impacting LP principal.

---

### Likelihood Explanation

Failure mode A is triggered by the default correct configuration (allowlist individual users, not the router). It affects every pool that deploys `SwapAllowlistExtension` and expects users to use the router.

Failure mode B is triggered when the pool admin takes the natural remediation step of allowlisting the router to restore router access — a reasonable and expected admin action with no documentation warning against it.

---

### Recommendation

`SwapAllowlistExtension` must gate the actual end-user, not the intermediary. Two viable approaches:

1. **Signed identity in `extensionData`**: Have the router embed `msg.sender` (the actual user) in `extensionData` and have `SwapAllowlistExtension` decode and verify it (with an optional signature or trusted-router flag).
2. **Router-aware forwarding**: Add a `swapWithSender(address actualSender, ...)` entry point on the pool (callable only by whitelisted routers) that passes `actualSender` instead of `msg.sender` to extension hooks.
3. **Separate router allowlist**: Distinguish between "router is allowed to relay" and "user is allowed to trade" by checking both `allowedSwapper[pool][router]` (relay permission) and a user identity extracted from `extensionData`.

---

### Proof of Concept

```solidity
// 1. Deploy pool with SwapAllowlistExtension as beforeSwap hook.
// 2. Admin allowlists userA only.
swapExt.setAllowedToSwap(address(pool), userA, true);

// 3. userA swaps directly — succeeds (sender = userA, allowedSwapper[pool][userA] = true).
vm.prank(userA);
pool.swap(userA, false, 1000, type(uint128).max, "", "");

// 4. userA swaps via router — REVERTS (sender = router, allowedSwapper[pool][router] = false).
vm.prank(userA);
router.exactInputSingle(ExactInputSingleParams({pool: address(pool), recipient: userA, ...}));
// → NotAllowedToSwap

// 5. Admin allowlists the router to fix userA's access.
swapExt.setAllowedToSwap(address(pool), address(router), true);

// 6. Non-allowlisted userB now bypasses the guard via router — SUCCEEDS.
vm.prank(userB);  // userB was never allowlisted
router.exactInputSingle(ExactInputSingleParams({pool: address(pool), recipient: userB, ...}));
// → swap executes; allowlist is fully bypassed
``` [1](#0-0) [2](#0-1) [3](#0-2) [4](#0-3)

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

**File:** metric-periphery/contracts/MetricOmmSimpleRouter.sol (L67-86)
```text
  function exactInputSingle(ExactInputSingleParams calldata params) external payable returns (uint256 amountOut) {
    _checkDeadline(params.deadline);
    uint128 priceLimitX64 = MetricOmmSwapPath.normalizePriceLimit(params.zeroForOne, params.priceLimitX64);

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
