I have enough information to produce the finding. Let me trace the full call path.

**Key trace:**

1. `SwapAllowlistExtension.beforeSwap` checks `allowedSwapper[msg.sender][sender]` — `msg.sender` is the pool (used as the key), `sender` is the first argument passed by the pool.
2. `ExtensionCalling._beforeSwap` passes `sender` (its own parameter) into the encoded call.
3. The pool's `swap` public function is called by the router; the pool passes `msg.sender` (= the router) as `sender` to `_beforeSwap`.
4. In `MetricOmmSimpleRouter.exactInputSingle` / `exactInput` / `exactOutputSingle` / `exactOutput`, the pool's `swap` is always called with `msg.sender` of the pool = the router contract.

Result: the allowlist always sees the router's address as the swapper, never the end user. For router-mediated swaps to work at all on an allowlisted pool, the admin must allowlist the router — which then lets **any** user bypass the allowlist by routing through it.

---

### Title
SwapAllowlistExtension Checks Router Address Instead of End User, Allowing Any Caller to Bypass the Swap Allowlist — (`metric-periphery/contracts/extensions/SwapAllowlistExtension.sol`)

### Summary
`SwapAllowlistExtension.beforeSwap` gates swaps by checking the `sender` argument passed from the pool. When a swap is routed through `MetricOmmSimpleRouter`, the pool's `msg.sender` is the router, so the extension always evaluates the router's address — not the actual end user. To allow any allowlisted user to swap via the router, the pool admin must add the router to the allowlist. Once the router is allowlisted, every address on the network can bypass the allowlist by calling the router.

### Finding Description
`SwapAllowlistExtension.beforeSwap` performs the identity check as:

```solidity
// SwapAllowlistExtension.sol L37
if (!allowAllSwappers[msg.sender] && !allowedSwapper[msg.sender][sender]) {
    revert IMetricOmmPoolActions.NotAllowedToSwap();
}
```

`msg.sender` here is the pool (correct pool key). `sender` is the first argument forwarded by `ExtensionCalling._beforeSwap`:

```solidity
// ExtensionCalling.sol L162-L176
abi.encodeCall(
    IMetricOmmExtensions.beforeSwap,
    (sender, recipient, zeroForOne, ...)
)
```

That `sender` originates from the pool's own `msg.sender` at the time `swap()` is called. When the user calls `MetricOmmSimpleRouter.exactInputSingle`:

```solidity
// MetricOmmSimpleRouter.sol L72-L80
(int128 amount0Delta, int128 amount1Delta) = IMetricOmmPoolActions(params.pool)
    .swap(
        params.recipient,
        params.zeroForOne,
        ...
        params.extensionData
    );
```

The pool's `msg.sender` is the router contract. The extension therefore evaluates `allowedSwapper[pool][router]`, not `allowedSwapper[pool][user]`. The same applies to `exactInput` (all hops), `exactOutputSingle`, and `exactOutput`.

### Impact Explanation
A pool deployer who wants to restrict swaps to a curated set of addresses (e.g., KYC'd wallets, protocol-owned addresses) deploys with `SwapAllowlistExtension`. To allow those users to swap via the canonical router, the admin must add the router to the allowlist. The moment the router is allowlisted, the guard is fully open: any address can call `exactInputSingle` or `exactInput` on the router and the extension will pass because it sees the allowlisted router address. The allowlist provides zero protection against non-allowlisted users on the router path. This is a direct, complete bypass of a core access-control mechanism with fund-impacting consequences (non-permitted actors can drain or manipulate curated pools).

### Likelihood Explanation
The router is the primary user-facing entry point documented and expected by the protocol. Any pool admin who deploys `SwapAllowlistExtension` and wants their allowlisted users to use the router will inevitably add the router to the allowlist, triggering the bypass. The attacker needs no special privilege — a single call to `exactInputSingle` suffices.

### Recommendation
Pass the original end-user address through the swap path so the extension can gate on it. Two options:

1. **Pool-level**: Have the pool accept an explicit `originator` parameter in `swap()` and forward it as `sender` to extensions, with the router populating it as `msg.sender` before calling the pool.
2. **Extension-level**: Change `SwapAllowlistExtension.beforeSwap` to check the `sender` argument only when it is not a known router, or require the router to pass the real user address in `extensionData` and decode it in the extension.

The `DepositAllowlistExtension` does not share this flaw because it gates on `owner` (the LP position beneficiary), which is explicitly supplied by the caller and is independent of the router address.

### Proof of Concept

```
Setup:
  - Deploy pool with SwapAllowlistExtension
  - Admin allowlists alice (KYC'd user) via setAllowedToSwap(pool, alice, true)
  - Admin allowlists router via setAllowedToSwap(pool, router, true)
    (required so alice can use the router)

Attack:
  - bob (not allowlisted) calls router.exactInputSingle({pool: pool, ...})
  - Router calls pool.swap(...) → pool.msg.sender = router
  - Pool calls extension.beforeSwap(sender=router, ...)
  - Extension checks allowedSwapper[pool][router] → true
  - Swap executes for bob despite bob not being on the allowlist

Result:
  - SwapAllowlistExtension is fully bypassed for any caller via the router
``` [1](#0-0) [2](#0-1) [3](#0-2) [4](#0-3) [5](#0-4)

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

**File:** metric-periphery/contracts/MetricOmmSimpleRouter.sol (L99-118)
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

      int128 amountInActual = MetricOmmSwapResults.extractAmountIn(zeroForOne, amount0Delta, amount1Delta);
      if (amountInActual < amount) revert InvalidInputAmountAtHop(uint8(i), amountInActual, amount);

      amount = MetricOmmSwapResults.extractAmountOut(zeroForOne, amount0Delta, amount1Delta);
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
