### Title
SwapAllowlistExtension Allowlist Bypassed via MetricOmmSimpleRouter — Any User Can Swap on Restricted Pools - (File: metric-periphery/contracts/extensions/SwapAllowlistExtension.sol)

### Summary

`SwapAllowlistExtension.beforeSwap` gates swaps by checking the `sender` argument, which the pool sets to `msg.sender` of the `pool.swap()` call. When a user routes through `MetricOmmSimpleRouter`, `msg.sender` inside the pool is the router contract, not the actual user. If the pool admin allowlists the router (required for any user to use the router on the restricted pool), every unprivileged user can bypass the allowlist by routing through the router.

### Finding Description

`MetricOmmPool.swap` passes `msg.sender` as the `sender` argument to `_beforeSwap`: [1](#0-0) 

`ExtensionCalling._beforeSwap` forwards that value unchanged to the extension: [2](#0-1) 

`SwapAllowlistExtension.beforeSwap` then checks `allowedSwapper[msg.sender][sender]`, where `msg.sender` is the pool and `sender` is the direct caller of `pool.swap()`: [3](#0-2) 

`MetricOmmSimpleRouter.exactInputSingle` calls `pool.swap()` directly without forwarding the originating user's address: [4](#0-3) 

The router stores the actual user (`msg.sender`) only in transient storage for the payment callback — it is never passed to the pool or to the extension: [5](#0-4) 

The same pattern holds for every multi-hop path: every `pool.swap()` call in `exactInput` and `exactOutput` is issued by the router, so `sender` seen by the extension is always the router address, never the originating user: [6](#0-5) 

The contract-level NatSpec for `SwapAllowlistExtension` states it "Gates `swap` by swapper address, per pool": [7](#0-6) 

Because the extension cannot distinguish individual users behind the router, the pool admin faces an impossible choice:

- **Do not allowlist the router** → no user can ever use the router on this pool (broken UX).
- **Allowlist the router** → every user, including non-allowlisted ones, can bypass the guard by routing through the router.

### Impact Explanation

Any user blocked by the `SwapAllowlistExtension` can bypass the restriction by calling `MetricOmmSimpleRouter.exactInputSingle` (or `exactInput` / `exactOutput`) against the restricted pool. The allowlist guard is rendered completely ineffective for router-mediated swaps. Pools configured for restricted participant sets (e.g., KYC-gated, institutional-only, or protocol-internal pools) are exposed to unauthorized swaps. Unauthorized swappers can extract value at the pool's oracle-driven prices, causing direct LP principal loss if the pool's pricing assumptions depend on a controlled participant set.

### Likelihood Explanation

The router is a public, permissionless contract. Any user who discovers the allowlist restriction can immediately route through the router instead of calling the pool directly. No special privileges, flash loans, or multi-block setup are required. The only precondition is that the pool admin has allowlisted the router — a step they must take if they want any legitimate user to use the router — making the bypass reachable in every realistic restricted-pool deployment that also supports router access.

### Recommendation

The `SwapAllowlistExtension` must gate on the economically relevant actor, not the proximate caller. Two sound approaches:

1. **Pass the originating user through `extensionData`**: The router encodes `msg.sender` into `extensionData` for each hop; the extension decodes and checks that address. The pool admin allowlists individual users, not the router.

2. **Check `tx.origin` as a fallback for EOA callers**: When `sender` is a known router (or any non-EOA), fall back to `tx.origin`. This is safe for EOA-only use cases and avoids the router-identity collapse.

The `DepositAllowlistExtension` correctly gates on `owner` (the position recipient) rather than `sender`, so it does not share this flaw: [8](#0-7) 

The swap extension should adopt the same principle: gate on the address that economically benefits from the swap (the originating user), not the address that mechanically calls the pool.

### Proof of Concept

```
Setup
─────
1. Pool admin deploys a pool with SwapAllowlistExtension as beforeSwap hook.
2. Pool admin calls setAllowedToSwap(pool, alice, true)   // alice is KYC'd
3. Pool admin calls setAllowedToSwap(pool, router, true)  // required for alice to use the router

Attack
──────
4. charlie (not allowlisted) calls:
       MetricOmmSimpleRouter.exactInputSingle({
           pool:          restrictedPool,
           ...
           extensionData: ""
       })

5. Router calls pool.swap(recipient, ...) — msg.sender inside pool = router.

6. Pool calls _beforeSwap(sender=router, ...).

7. SwapAllowlistExtension.beforeSwap checks:
       allowedSwapper[pool][router]  →  true   ✓

8. Swap executes. charlie receives output tokens.
   The allowlist guard was never consulted for charlie's identity.
```

The bypass requires zero privileged access and is reachable in a single transaction.

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

**File:** metric-periphery/contracts/extensions/SwapAllowlistExtension.sol (L9-11)
```text
/// @title SwapAllowlistExtension
/// @notice Gates `swap` by swapper address, per pool.
contract SwapAllowlistExtension is BaseMetricExtension, ISwapAllowlistExtension {
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

**File:** metric-periphery/contracts/MetricOmmSimpleRouter.sol (L99-112)
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
