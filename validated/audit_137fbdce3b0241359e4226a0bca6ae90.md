### Title
SwapAllowlistExtension Gates on Router Address Instead of Actual User, Enabling Full Allowlist Bypass via MetricOmmSimpleRouter — (`metric-periphery/contracts/extensions/SwapAllowlistExtension.sol`)

---

### Summary

`SwapAllowlistExtension.beforeSwap` checks the `sender` argument, which the pool sets to `msg.sender` of the `pool.swap()` call. When a user routes through `MetricOmmSimpleRouter`, `msg.sender` at the pool is the **router**, not the user. If the router is allowlisted (the only way legitimate users can use it), every unpermissioned address can bypass the per-user allowlist by routing through the router.

---

### Finding Description

`MetricOmmPool.swap` passes `msg.sender` as the `sender` argument to `_beforeSwap`: [1](#0-0) 

`ExtensionCalling._beforeSwap` forwards that value verbatim to the extension: [2](#0-1) 

`SwapAllowlistExtension.beforeSwap` then checks `allowedSwapper[msg.sender][sender]`, where `msg.sender` is the pool and `sender` is whoever called `pool.swap()`: [3](#0-2) 

`MetricOmmSimpleRouter.exactInputSingle` (and every other router entry point) calls `pool.swap()` directly, making the router the `msg.sender` at the pool: [4](#0-3) 

This creates an irresolvable dilemma for any pool admin who deploys a swap allowlist:

| Router allowlist state | Effect |
|---|---|
| Router **not** allowlisted | Allowlisted users cannot use the router at all |
| Router **allowlisted** | Every address on-chain can bypass the per-user allowlist by routing through the router |

There is no configuration that simultaneously allows allowlisted users to use the router and blocks non-allowlisted users from doing the same.

---

### Impact Explanation

A pool admin deploys `SwapAllowlistExtension` to restrict swaps to a set of KYC-verified or otherwise privileged addresses. Any non-allowlisted attacker calls `MetricOmmSimpleRouter.exactInputSingle` (or `exactInput`, `exactOutputSingle`, `exactOutput`) targeting the restricted pool. Because the router is allowlisted (required for legitimate users), the extension sees `sender = router` and passes the check. The attacker executes swaps against the pool's liquidity as if they were a permitted user, draining LP value or executing toxic flow that the allowlist was designed to prevent.

The same bypass applies to `simulateSwapAndRevert`, which also calls `_beforeSwap(msg.sender, ...)`. [5](#0-4) 

---

### Likelihood Explanation

- The router is a public, permissionless contract deployed by the protocol.
- Any pool that uses `SwapAllowlistExtension` with `allowAllSwappers = false` is affected the moment the router is allowlisted.
- No privileged access, no special tokens, no malicious setup: a standard `exactInputSingle` call is sufficient.
- The attacker only needs to know the pool address and that the router is allowlisted.

---

### Recommendation

Pass the **original user** through the swap path rather than the immediate caller. Two options:

1. **Preferred — record the originating user in the router and forward it via `extensionData`**: The extension reads the true user from `extensionData` instead of `sender`. This requires a coordinated change to the extension interface.

2. **Simpler — add a `msgSender` field to the pool's swap interface**: The pool accepts an explicit `swapper` address that the router sets to `msg.sender` before calling the pool, and the pool passes that to the extension instead of its own `msg.sender`. The pool must verify the caller is a trusted router or that `swapper == msg.sender` for direct calls.

The `DepositAllowlistExtension` does **not** share this flaw — it correctly gates on `owner` (the position owner), which is explicitly passed and not collapsed by the liquidity adder. [6](#0-5) 

---

### Proof of Concept

```
Setup:
  1. Deploy pool with SwapAllowlistExtension; set allowAllSwappers[pool] = false.
  2. allowedSwapper[pool][alice] = true   // alice is the only permitted swapper
  3. allowedSwapper[pool][router] = true  // required so alice can use the router

Attack (executed by bob, a non-allowlisted address):
  4. bob calls MetricOmmSimpleRouter.exactInputSingle({
         pool: restrictedPool,
         recipient: bob,
         zeroForOne: true,
         amountIn: X,
         ...
     })
  5. Router calls pool.swap(bob, true, X, ...) → msg.sender at pool = router
  6. Pool calls _beforeSwap(sender=router, ...)
  7. Extension checks allowedSwapper[pool][router] → true → passes
  8. Swap executes; bob receives output tokens.

Result: bob, a non-allowlisted address, successfully swaps against a pool
        that was configured to block all non-allowlisted swappers.
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

**File:** metric-core/contracts/MetricOmmPool.sol (L319-332)
```text
    uint256 packedSlot0Initial = Slot0Library.loadPackedSlot0();

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
