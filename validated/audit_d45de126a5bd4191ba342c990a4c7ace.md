### Title
SwapAllowlistExtension Checks Router Address Instead of Actual User, Enabling Full Allowlist Bypass via MetricOmmSimpleRouter — (`metric-periphery/contracts/extensions/SwapAllowlistExtension.sol`)

---

### Summary

`SwapAllowlistExtension.beforeSwap` gates swaps by checking the `sender` argument it receives from the pool. The pool always passes `msg.sender` (its direct caller) as `sender`. When a user routes through `MetricOmmSimpleRouter`, the pool's `msg.sender` is the router contract, not the end user. If the pool admin allowlists the router address so that legitimate users can reach the pool through the supported periphery path, every unpermissioned user can bypass the per-user allowlist by routing through the same router.

---

### Finding Description

**Call chain for a direct swap (works as intended):**

```
User → pool.swap(msg.sender=User) → _beforeSwap(sender=User) → extension checks allowedSwapper[pool][User]
```

**Call chain for a router swap (broken):**

```
User → MetricOmmSimpleRouter.exactInputSingle() → pool.swap(msg.sender=Router) → _beforeSwap(sender=Router) → extension checks allowedSwapper[pool][Router]
```

In `MetricOmmPool.swap`, the pool unconditionally passes `msg.sender` as the `sender` argument to `_beforeSwap`: [1](#0-0) 

`ExtensionCalling._beforeSwap` forwards that value unchanged to every configured extension: [2](#0-1) 

`SwapAllowlistExtension.beforeSwap` then checks `allowedSwapper[msg.sender][sender]`, where `msg.sender` is the pool and `sender` is whatever the pool forwarded — the router address when the call came through the router: [3](#0-2) 

`MetricOmmSimpleRouter.exactInputSingle` calls `pool.swap` with no user-identity forwarding; the pool sees only the router as `msg.sender`: [4](#0-3) 

The same pattern applies to `exactInput`, `exactOutputSingle`, and `exactOutput`. [5](#0-4) 

---

### Impact Explanation

A pool admin who deploys a `SwapAllowlistExtension` intends to restrict swaps to a curated set of addresses. To let those addresses use the supported router periphery, the admin must add the router to the allowlist (`setAllowedToSwap(pool, router, true)`). Once the router is allowlisted, the check degenerates to "is the router allowed?" — which is always true — and every unpermissioned user can swap by calling `MetricOmmSimpleRouter` instead of the pool directly. The per-user allowlist is completely neutralized. Unauthorized users gain full swap access to a pool that was designed to be curated, enabling them to trade against LP liquidity at oracle-anchored prices without the pool admin's consent. This is a direct loss-of-control over LP assets and constitutes a broken core pool functionality / admin-boundary break.

---

### Likelihood Explanation

The `MetricOmmSimpleRouter` is the primary supported swap entry point documented in the periphery. Any pool admin who configures a `SwapAllowlistExtension` and also wants their allowlisted users to use the router will naturally allowlist the router address. The precondition (router is allowlisted) is the expected operational state for any production pool that uses both the allowlist extension and the router. No privileged attacker role is required; any EOA can call the router.

---

### Recommendation

The `SwapAllowlistExtension` must gate on the **economic actor** (the end user), not the intermediary. Two complementary fixes:

1. **Pass the original user through the router.** The router should forward the originating `msg.sender` in the `extensionData` or a dedicated field, and the extension should decode and verify it. Alternatively, the pool interface could accept an explicit `originator` argument.

2. **Check `sender` only when it is not a trusted router, and check the decoded originator otherwise.** The extension can maintain a registry of trusted routers and, when `sender` is a trusted router, require the extension payload to carry a signed or ABI-encoded originator address.

The simplest safe fix is to have the router pass `msg.sender` (the end user) as the `sender` to the pool, which requires a pool-level change to accept an explicit originator, or to have the extension decode the originator from `extensionData` when the direct `sender` is a known router.

---

### Proof of Concept

```solidity
// Setup: pool with SwapAllowlistExtension; only `allowedUser` is allowlisted.
// Admin also allowlists the router so allowedUser can use it.
swapExtension.setAllowedToSwap(address(pool), allowedUser, true);
swapExtension.setAllowedToSwap(address(pool), address(router), true); // required for router use

// Attack: bannedUser routes through the router.
vm.startPrank(bannedUser);
token0.approve(address(router), type(uint256).max);
// This succeeds: pool sees msg.sender=router, extension checks allowedSwapper[pool][router] == true
router.exactInputSingle(IMetricOmmSimpleRouter.ExactInputSingleParams({
    pool: address(pool),
    recipient: bannedUser,
    tokenIn: address(token0),
    zeroForOne: true,
    amountIn: 1000,
    amountOutMinimum: 0,
    priceLimitX64: 0,
    deadline: block.timestamp,
    extensionData: ""
}));
// bannedUser successfully swapped on a pool they were never allowlisted for.
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

**File:** metric-periphery/contracts/MetricOmmSimpleRouter.sol (L92-125)
```text
  function exactInput(ExactInputParams calldata params) external payable returns (uint256 amountOut) {
    _checkDeadline(params.deadline);
    _validatePath(params.tokens, params.pools, params.extensionDatas);

    uint256 last = params.pools.length - 1;
    int128 amount = MetricOmmSwapInputs.asAmountSpecifiedIn(params.amountIn);

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

      int128 amountInActual = MetricOmmSwapResults.extractAmountIn(zeroForOne, amount0Delta, amount1Delta);
      if (amountInActual < amount) revert InvalidInputAmountAtHop(uint8(i), amountInActual, amount);

      amount = MetricOmmSwapResults.extractAmountOut(zeroForOne, amount0Delta, amount1Delta);
    }

    if (amount <= 0) revert InvalidSwapDeltas();
    amountOut = MetricOmmSwapInputs.int128ToUint128(amount);
    if (amountOut < params.amountOutMinimum) revert InsufficientOutput(amountOut, params.amountOutMinimum);

    _clearExpectedCallbackPool();
  }
```
