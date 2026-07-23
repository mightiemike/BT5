### Title
SwapAllowlistExtension gates the router address instead of the actual swapper, enabling full allowlist bypass via MetricOmmSimpleRouter — (`metric-periphery/contracts/extensions/SwapAllowlistExtension.sol`)

---

### Summary

`SwapAllowlistExtension.beforeSwap` checks the `sender` argument supplied by the pool, which is the pool's `msg.sender` (i.e., the direct caller of `pool.swap()`). When a user routes through `MetricOmmSimpleRouter`, the router is the direct caller, so the allowlist check is performed against the router's address rather than the actual end user. If the pool admin allowlists the router — a natural action to let allowlisted users access the pool through the periphery — any unprivileged user can bypass the restriction by routing through the public router.

---

### Finding Description

`SwapAllowlistExtension.beforeSwap` enforces:

```solidity
if (!allowAllSwappers[msg.sender] && !allowedSwapper[msg.sender][sender]) {
    revert IMetricOmmPoolActions.NotAllowedToSwap();
}
``` [1](#0-0) 

Here `msg.sender` is the pool (the extension is called by the pool) and `sender` is the first argument forwarded by `ExtensionCalling._beforeSwap`, which the pool sets to its own `msg.sender` — the direct caller of `pool.swap()`. [2](#0-1) 

When a user calls `MetricOmmSimpleRouter.exactInputSingle()`, the router calls `pool.swap(params.recipient, ...)` directly: [3](#0-2) 

The pool's `msg.sender` is now the router, so `sender = router_address` is what reaches the extension. The allowlist check becomes `allowedSwapper[pool][router]` instead of `allowedSwapper[pool][actual_user]`.

**Bypass path:**

1. Pool admin deploys a pool with `SwapAllowlistExtension` and allowlists specific users: `allowedSwapper[pool][user1] = true`.
2. Allowlisted users need to use the router (e.g., for multi-hop or slippage protection), so the admin also sets `allowedSwapper[pool][router] = true`.
3. A non-allowlisted `attacker` calls `MetricOmmSimpleRouter.exactInputSingle({pool: restrictedPool, ...})`.
4. The pool calls `_beforeSwap(sender=router, ...)` → extension checks `allowedSwapper[pool][router]` → `true` → swap proceeds.
5. The attacker successfully swaps in a pool that was intended to be restricted.

The same bypass applies to `exactInput`, `exactOutputSingle`, and `exactOutput` on the router. [4](#0-3) 

The `DepositAllowlistExtension` does **not** share this flaw because it checks the `owner` argument (the position owner explicitly passed to `addLiquidity`), not `sender`. [5](#0-4) 

---

### Impact Explanation

Any user can execute swaps in a pool that the admin intended to restrict to a specific allowlist, by routing through the public `MetricOmmSimpleRouter`. This directly violates the pool's access control invariant. Consequences include:

- Unauthorized counterparties executing swaps against LP liquidity in a private/institutional pool.
- LP funds exposed to traders the pool admin explicitly excluded (e.g., for risk or regulatory reasons).
- The allowlist guard is rendered completely ineffective once the router is allowlisted, which is the only way to let legitimate allowlisted users use the periphery.

This is a direct loss of LP principal control and a broken core pool functionality (access-gated swap).

---

### Likelihood Explanation

The trigger is unprivileged: any user can call `MetricOmmSimpleRouter` with any pool address. The only precondition is that the pool admin has allowlisted the router, which is a natural and expected operational step whenever allowlisted users need to use the periphery. The admin has no way to simultaneously allow allowlisted users to use the router and block non-allowlisted users from doing the same, given the current design.

---

### Recommendation

The extension must identify the true end user, not the intermediary. Two options:

1. **Pass the real user through `extensionData`**: The router encodes `msg.sender` into `extensionData`; the extension decodes and checks it. This requires a trusted encoding convention.

2. **Check `recipient` instead of `sender`**: For single-hop swaps the recipient is often the real user, though this breaks for multi-hop paths where intermediate recipients are the router itself.

3. **Preferred — dedicated identity field**: Add a `swapper` field to the pool's swap interface (analogous to how `owner` is explicit in `addLiquidity`) so the pool can forward the true initiator independently of `msg.sender`.

Until fixed, pool admins should not use `SwapAllowlistExtension` with any router-allowlisted configuration, as it provides no effective access control.

---

### Proof of Concept

```solidity
// Setup: pool with SwapAllowlistExtension, only user1 and router allowlisted
swapAllowlist.setAllowedToSwap(pool, user1, true);
swapAllowlist.setAllowedToSwap(pool, address(router), true); // needed for user1 to use router

// Attacker (not allowlisted) bypasses via router
vm.prank(attacker);
router.exactInputSingle(IMetricOmmSimpleRouter.ExactInputSingleParams({
    pool: address(restrictedPool),
    recipient: attacker,
    zeroForOne: true,
    amountIn: 1000e18,
    amountOutMinimum: 0,
    priceLimitX64: 0,
    tokenIn: token0,
    deadline: block.timestamp,
    extensionData: ""
}));
// Succeeds: pool sees sender=router, allowedSwapper[pool][router]=true → no revert
// Attacker swaps in a pool they were never supposed to access
```

### Citations

**File:** metric-periphery/contracts/extensions/SwapAllowlistExtension.sol (L37-39)
```text
    if (!allowAllSwappers[msg.sender] && !allowedSwapper[msg.sender][sender]) {
      revert IMetricOmmPoolActions.NotAllowedToSwap();
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
