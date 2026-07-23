### Title
SwapAllowlistExtension Bypass via Router: Any User Can Swap on Allowlisted Pools When Router Is Allowlisted - (`metric-periphery/contracts/extensions/SwapAllowlistExtension.sol`)

---

### Summary

`SwapAllowlistExtension.beforeSwap` gates swaps by checking the `sender` argument passed by the pool. When `MetricOmmSimpleRouter` calls `pool.swap()`, the pool records `msg.sender` (the router) as `sender` and forwards it to the extension. If the pool admin allowlists the router address to enable router-mediated swaps, the allowlist is completely bypassed: any unprivileged user can swap on the restricted pool simply by routing through the public router.

---

### Finding Description

`MetricOmmPool.swap()` calls `_beforeSwap(msg.sender, recipient, ...)`, passing its own `msg.sender` — the direct caller — as the `sender` argument to every configured extension. [1](#0-0) 

`SwapAllowlistExtension.beforeSwap` then checks that identity against the per-pool allowlist:

```solidity
function beforeSwap(address sender, address, bool, int128, uint128, uint256, uint128, uint128, bytes calldata)
    external view override returns (bytes4)
{
    if (!allowAllSwappers[msg.sender] && !allowedSwapper[msg.sender][sender]) {
        revert IMetricOmmPoolActions.NotAllowedToSwap();
    }
    return IMetricOmmExtensions.beforeSwap.selector;
}
``` [2](#0-1) 

Here `msg.sender` is the pool (correct) and `sender` is whoever called `pool.swap()`. When `MetricOmmSimpleRouter.exactInputSingle` (or any other router entry point) calls `pool.swap()`, the router is `msg.sender` of the pool call, so `sender = router`. [3](#0-2) 

The allowlist check therefore evaluates `allowedSwapper[pool][router]`, not `allowedSwapper[pool][actual_user]`.

A pool admin who wants allowlisted users to be able to use the router must add the router to the allowlist. Once the router is allowlisted, the check `allowedSwapper[pool][router] == true` passes for **every** caller of the router, regardless of whether that caller is on the allowlist. The router is a public, permissionless contract with no caller-identity forwarding.

The same structural problem applies to the multi-hop `exactInput` path: [4](#0-3) 

and the exact-output recursive callback path: [5](#0-4) 

In every case the pool sees the router as `msg.sender`, so the extension sees the router as `sender`.

---

### Impact Explanation

A pool configured with `SwapAllowlistExtension` to restrict trading to a specific set of addresses (e.g., KYC-verified counterparties, institutional LPs, or whitelisted market makers) loses that restriction entirely for any user who routes through `MetricOmmSimpleRouter`. The attacker receives real token output from the pool's LP reserves without being on the allowlist, directly harming LPs who deposited under the assumption that only vetted counterparties could trade against them. [6](#0-5) 

---

### Likelihood Explanation

The bypass requires the pool admin to have added the router to the allowlist. This is the natural and expected configuration for any pool that (a) uses `SwapAllowlistExtension` to restrict direct swaps and (b) still wants allowlisted users to be able to use the standard router UI. The pool admin has no way to simultaneously allow router-mediated swaps for allowlisted users and block router-mediated swaps for non-allowlisted users under the current design, so the bypass is reachable in any production deployment that uses the router alongside the allowlist extension. [7](#0-6) 

---

### Recommendation

The extension must be able to distinguish the economic actor (the end user) from the routing intermediary. Two complementary fixes:

1. **Pass the originating user through `extensionData`**: The router should encode `msg.sender` (the actual user) into `extensionData` for each hop. `SwapAllowlistExtension.beforeSwap` can then decode and check that address when `sender` is a known router. This requires a convention between the router and the extension.

2. **Check `sender` only, never the router**: Alternatively, the pool interface could be extended so that the router passes the originating user as an explicit `payer` or `originator` field that the pool forwards to extensions as a separate argument, keeping `sender` as the direct caller for callback purposes only.

Until fixed, pool admins should not add the router to the allowlist; instead they should require allowlisted users to call `pool.swap()` directly.

---

### Proof of Concept

```
Setup:
  - Deploy pool with SwapAllowlistExtension as beforeSwap hook.
  - Pool admin calls setAllowedToSwap(pool, router, true)
    (to let allowlisted users reach the pool via the router).
  - Pool admin does NOT call setAllowedToSwap(pool, attacker, true).

Attack:
  1. attacker (not on allowlist) calls
       router.exactInputSingle({ pool: pool, ... })
  2. Router calls pool.swap(recipient, ...) — msg.sender = router.
  3. Pool calls _beforeSwap(sender=router, ...).
  4. Extension evaluates allowedSwapper[pool][router] == true → passes.
  5. Swap executes; attacker receives token output from LP reserves.

Expected: revert NotAllowedToSwap (attacker not on allowlist).
Actual:   swap succeeds; allowlist guard is fully bypassed.
``` [8](#0-7) [9](#0-8)

### Citations

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

**File:** metric-periphery/contracts/extensions/SwapAllowlistExtension.sol (L12-13)
```text
  mapping(address pool => mapping(address swapper => bool)) public allowedSwapper;
  mapping(address pool => bool) public allowAllSwappers;
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

**File:** metric-periphery/contracts/extensions/base/BaseMetricExtension.sol (L19-24)
```text
  modifier onlyPool() {
    if (!IMetricOmmPoolFactory(FACTORY).isPool(msg.sender)) {
      revert OnlyPool(msg.sender, FACTORY);
    }
    _;
  }
```
