### Title
SwapAllowlistExtension Checks Router Address Instead of End User, Allowing Any Caller to Bypass the Swap Allowlist via MetricOmmSimpleRouter — (`metric-periphery/contracts/extensions/SwapAllowlistExtension.sol`)

---

### Summary

`SwapAllowlistExtension.beforeSwap` gates swaps by checking `allowedSwapper[pool][sender]`, where `sender` is `msg.sender` from the pool's perspective. When a user routes through `MetricOmmSimpleRouter`, `sender` becomes the router address, not the end user. If the pool admin allowlists the router (the natural step to support router-mediated swaps for their curated users), every public caller can bypass the allowlist entirely by routing through the router.

---

### Finding Description

`SwapAllowlistExtension.beforeSwap` receives `sender` as the first argument, which the pool sets to `msg.sender` of the `swap()` call:

```solidity
// MetricOmmPool.sol – swap()
_beforeSwap(
  msg.sender,   // ← this becomes `sender` in the extension
  recipient,
  ...
);
```

The extension then checks:

```solidity
// SwapAllowlistExtension.sol line 37
if (!allowAllSwappers[msg.sender] && !allowedSwapper[msg.sender][sender]) {
  revert IMetricOmmPoolActions.NotAllowedToSwap();
}
```

`msg.sender` here is the pool; `sender` is whoever called `pool.swap()`. When a user calls `MetricOmmSimpleRouter.exactInputSingle` (or any other router entry point), the router calls `pool.swap()` directly:

```solidity
// MetricOmmSimpleRouter.sol line 72-80
IMetricOmmPoolActions(params.pool).swap(
  params.recipient,
  params.zeroForOne,
  MetricOmmSwapInputs.asAmountSpecifiedIn(params.amountIn),
  priceLimitX64,
  "",
  params.extensionData
);
```

So `sender` in the extension = router address, not the end user. The allowlist check becomes `allowedSwapper[pool][router]`.

**Attack path:**

1. Pool admin deploys a curated pool with `SwapAllowlistExtension` to restrict swaps to specific counterparties.
2. Pool admin allowlists individual users: `allowedSwapper[pool][user1] = true`.
3. Pool admin also allowlists the router so that allowlisted users can use the router: `allowedSwapper[pool][router] = true`.
4. Any unpermissioned user calls `router.exactInputSingle({pool: curated_pool, ...})`.
5. The extension sees `sender = router`, finds `allowedSwapper[pool][router] = true`, and passes. The swap executes.
6. The allowlist is completely bypassed.

The pool admin has no way to allowlist the router for their legitimate users without simultaneously opening the gate to all users, because the extension has no mechanism to inspect the original caller of the router.

---

### Impact Explanation

A curated pool's swap allowlist is its primary access-control boundary. Bypassing it allows unauthorized counterparties to trade against LP liquidity. Depending on the pool's purpose:

- **Compliance-gated pools** (KYC/AML): unauthorized users trade freely.
- **LP-protection pools** (restricted to trusted market makers): adversarial traders can execute swaps at oracle-anchored prices, extracting value from LP positions through adverse selection or timing attacks.

This is a direct loss of LP principal: the pool's liquidity is exposed to counterparties the pool admin explicitly intended to exclude.

---

### Likelihood Explanation

The trigger requires the pool admin to allowlist the router. This is the natural operational step any pool admin takes when they want their allowlisted users to be able to use the standard periphery router (rather than forcing them to call the pool directly). The admin has no indication from the code or documentation that allowlisting the router opens the gate to all users. The bypass is therefore reachable through normal, expected pool administration.

---

### Recommendation

The extension must check the actual end user, not the intermediary. Two approaches:

1. **Pass the original caller through `extensionData`**: The router encodes `msg.sender` into `extensionData`, and the extension decodes and checks it. This requires router cooperation and is fragile.

2. **Check `sender` against the allowlist and require direct pool calls for allowlisted pools**: Document that the router cannot be used with allowlisted pools, and add a guard in the extension that reverts if `sender` is a known router.

3. **Preferred — check both `sender` and the decoded original caller**: The router should encode the original `msg.sender` into `extensionData`, and the extension should decode and verify it when `sender` is a known router address.

---

### Proof of Concept

```solidity
// Setup: curated pool with SwapAllowlistExtension
// Pool admin allowlists user1 and the router
swapExtension.setAllowedToSwap(address(pool), user1, true);
swapExtension.setAllowedToSwap(address(pool), address(router), true);

// user2 is NOT allowlisted
// Direct swap by user2 reverts:
vm.prank(user2);
vm.expectRevert(IMetricOmmPoolActions.NotAllowedToSwap.selector);
pool.swap(user2, false, 1000, type(uint128).max, "", "");

// But user2 routes through the router — succeeds because sender = router:
vm.prank(user2);
router.exactInputSingle(
  IMetricOmmSimpleRouter.ExactInputSingleParams({
    pool: address(pool),
    recipient: user2,
    tokenIn: address(token0),
    zeroForOne: false,
    amountIn: 1000,
    amountOutMinimum: 0,
    priceLimitX64: type(uint128).max,
    deadline: block.timestamp + 1,
    extensionData: ""
  })
);
// Swap succeeds — allowlist bypassed
```

The root cause is in `SwapAllowlistExtension.beforeSwap` at line 37, where `sender` is the router address when the user enters through `MetricOmmSimpleRouter`, and the allowlist maps `allowedSwapper[pool][router]` rather than `allowedSwapper[pool][end_user]`. [1](#0-0) [2](#0-1) [3](#0-2) [4](#0-3)

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

**File:** metric-core/contracts/MetricOmmPool.sol (L228-240)
```text
    (uint128 bidPriceX64, uint128 askPriceX64) = _getBidAndAskPriceX64();

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

**File:** metric-periphery/contracts/MetricOmmSimpleRouter.sol (L71-86)
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
    int128 out = MetricOmmSwapResults.extractAmountOut(params.zeroForOne, amount0Delta, amount1Delta);
    amountOut = MetricOmmSwapInputs.int128ToUint128(out);
    if (amountOut < params.amountOutMinimum) revert InsufficientOutput(amountOut, params.amountOutMinimum);

    _clearExpectedCallbackPool();
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
