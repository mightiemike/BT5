Audit Report

## Title
`SwapAllowlistExtension` Allowlist Fully Bypassed via `MetricOmmSimpleRouter` — (`metric-periphery/contracts/extensions/SwapAllowlistExtension.sol`)

## Summary
`SwapAllowlistExtension.beforeSwap` gates swaps by checking the `sender` argument, which the pool sets to its own `msg.sender`. When `MetricOmmSimpleRouter` intermediates a swap, the pool's `msg.sender` is the router contract, not the end user. Any pool admin who allowlists the router to support router-mediated swaps for legitimate users simultaneously opens the gate to every unpermissioned user who calls the same public router.

## Finding Description
**Call path — pool to extension:**

`MetricOmmPool.swap` passes its own `msg.sender` as the `sender` argument to `_beforeSwap`: [1](#0-0) 

`ExtensionCalling._beforeSwap` forwards that value unchanged into the encoded extension call: [2](#0-1) 

**What the allowlist checks:**

`SwapAllowlistExtension.beforeSwap` gates on `allowedSwapper[msg.sender][sender]`, where `msg.sender` is the pool and `sender` is whoever called `pool.swap()`: [3](#0-2) 

**The router is the direct caller of `pool.swap()`:**

Every public entry point in `MetricOmmSimpleRouter` calls `pool.swap()` directly, making the router the `msg.sender` the pool sees: [4](#0-3) [5](#0-4) [6](#0-5) [7](#0-6) 

**The irreconcilable dilemma:**

- If the admin does **not** allowlist the router → allowlisted users cannot use the router at all (router address fails the check).
- If the admin **allowlists the router** → `allowedSwapper[pool][router] == true`, so every call through the router passes, regardless of who the end user is.

There is no mechanism in the extension or the pool to recover the original end-user identity once the router is the direct caller of `pool.swap()`. The `allowedSwapper` mapping only stores per-pool, per-address booleans: [8](#0-7) 

## Impact Explanation
The swap allowlist invariant — "only allowlisted addresses may swap in this pool" — is completely broken for any pool whose admin allowlists the router. Any unpermissioned user can execute swaps against a supposedly restricted pool by routing through `MetricOmmSimpleRouter`. This is a broken core pool access-control mechanism with direct fund-flow consequences: unauthorized parties can drain liquidity via swaps. This matches the allowed impact category of "Broken core pool functionality causing loss of funds" and "Admin-boundary break bypassed by an unprivileged path." Severity: Medium.

## Likelihood Explanation
The router is the standard periphery entry point for slippage-protected and multi-hop swaps. A pool admin who wants to restrict swaps to a curated set of users but still allow those users to use the router will naturally allowlist the router. The bypass requires no special privileges, no flash loans, and no unusual token behavior — any EOA can call `MetricOmmSimpleRouter.exactInputSingle()` or any other router entry point.

## Recommendation
The extension must gate on the end user's identity, not the intermediary's. Two viable approaches:

1. **Pass end-user identity through `extensionData`**: The router encodes `msg.sender` into `extensionData`; the extension decodes and checks it. This is only safe if the router is a verified, non-upgradeable contract that the extension explicitly trusts.
2. **Check `sender` at the router level before calling the pool**: The router maintains its own allowlist and reverts before forwarding to the pool. The pool-level extension then only needs to gate direct pool callers.

## Proof of Concept
```
1. Deploy pool with SwapAllowlistExtension configured.
2. Admin calls setAllowedToSwap(pool, alice, true)       // alice is the intended gated user
3. Admin calls setAllowedToSwap(pool, router, true)      // router allowlisted so alice can use it
4. charlie (not allowlisted) calls:
       MetricOmmSimpleRouter.exactInputSingle(
           {pool, recipient, zeroForOne, amountIn, amountOutMinimum, priceLimitX64, tokenIn, extensionData, deadline}
       )
5. Router calls pool.swap(recipient, ...) with msg.sender = router.
6. Pool calls _beforeSwap(sender=router, ...).
7. Extension checks: allowedSwapper[pool][router] == true  → PASSES.
8. Charlie's swap executes against the restricted pool.
```
The bypass requires zero special access — only a public router call.

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

**File:** metric-periphery/contracts/extensions/SwapAllowlistExtension.sol (L11-29)
```text
contract SwapAllowlistExtension is BaseMetricExtension, ISwapAllowlistExtension {
  mapping(address pool => mapping(address swapper => bool)) public allowedSwapper;
  mapping(address pool => bool) public allowAllSwappers;

  constructor(address factory_) BaseMetricExtension(factory_) {}

  function setAllowedToSwap(address pool_, address swapper, bool allowed) external onlyPoolAdmin(pool_) {
    allowedSwapper[pool_][swapper] = allowed;
    emit AllowedToSwapSet(pool_, swapper, allowed);
  }

  function setAllowAllSwappers(address pool_, bool allowed) external onlyPoolAdmin(pool_) {
    allowAllSwappers[pool_] = allowed;
    emit AllowAllSwappersSet(pool_, allowed);
  }

  function isAllowedToSwap(address pool_, address swapper) external view returns (bool) {
    return allowAllSwappers[pool_] || allowedSwapper[pool_][swapper];
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

**File:** metric-periphery/contracts/MetricOmmSimpleRouter.sol (L104-112)
```text
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

**File:** metric-periphery/contracts/MetricOmmSimpleRouter.sol (L136-137)
```text
    (int128 amount0Delta, int128 amount1Delta) = IMetricOmmPoolActions(params.pool)
      .swap(params.recipient, params.zeroForOne, -expectedAmountOut, priceLimitX64, "", params.extensionData);
```

**File:** metric-periphery/contracts/MetricOmmSimpleRouter.sol (L165-181)
```text
    (int128 amount0Delta, int128 amount1Delta) = IMetricOmmPoolActions(pool)
      .swap(
        params.recipient,
        zeroForOne,
        -expectedAmountOut,
        MetricOmmSwapPath.openLimit(zeroForOne),
        abi.encode(
          ExactOutputIterateCallbackData({
          tokens: params.tokens,
          pools: params.pools,
          extensionDatas: params.extensionDatas,
          zeroForOneBitMap: params.zeroForOneBitMap,
          amountInMax: params.amountInMaximum
        })
        ),
        params.extensionDatas[tradesLeftAfterThis]
      );
```
