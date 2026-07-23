### Title
SwapAllowlistExtension Gates the Router Address Instead of the Originating User, Blocking Allowlisted Router Swaps and Enabling Full Allowlist Bypass - (File: metric-periphery/contracts/extensions/SwapAllowlistExtension.sol)

### Summary
`SwapAllowlistExtension.beforeSwap()` checks `allowedSwapper[pool][sender]` where `sender` is `msg.sender` of the pool's `swap()` call. When a user routes through `MetricOmmSimpleRouter`, the pool's `swap()` is called by the router, so `sender` = router address — not the originating user. This produces two mutually exclusive broken states: (1) allowlisted users cannot swap via the router because the router is not on the allowlist, or (2) if the pool admin allowlists the router to fix (1), every unprivileged user can bypass the allowlist entirely by routing through the router.

### Finding Description

`MetricOmmPool.swap()` passes `msg.sender` as the `sender` argument to `_beforeSwap()`: [1](#0-0) 

`ExtensionCalling._beforeSwap()` forwards that value unchanged as the first argument to every configured extension: [2](#0-1) 

`SwapAllowlistExtension.beforeSwap()` then checks whether that `sender` is on the per-pool allowlist: [3](#0-2) 

`MetricOmmSimpleRouter.exactInputSingle()` calls `pool.swap()` directly — so `msg.sender` seen by the pool is the router, not the end user: [4](#0-3) 

The same holds for every other router entry point (`exactInput`, `exactOutputSingle`, `exactOutput`), and for intermediate hops inside `_exactOutputIterateCallback` where the router again calls `pool.swap()`: [5](#0-4) 

The allowlist mapping is keyed `allowedSwapper[pool][swapper]` and is populated per individual address by the pool admin: [6](#0-5) 

Because the router is a single shared contract, the pool admin faces an impossible choice:

- **Do not allowlist the router** → every allowlisted user who calls any `MetricOmmSimpleRouter` entry point has their swap rejected with `NotAllowedToSwap`, even though they are individually permitted. Core swap functionality is broken for the intended user set.
- **Allowlist the router** → `allowedSwapper[pool][router] = true` satisfies the check for every call that arrives through the router, regardless of who the originating EOA is. Any unprivileged user can bypass the allowlist by calling the router.

### Impact Explanation

**Scenario A (router not allowlisted):** Allowlisted users cannot execute swaps through the public router. The router is the primary user-facing swap entry point; blocking it makes the pool's swap flow unusable for the intended participant set — a broken core pool functionality impact.

**Scenario B (router allowlisted to restore router access):** The allowlist invariant is fully broken. Any address can call `MetricOmmSimpleRouter.exactInputSingle()` (or any other router function) and the `beforeSwap` hook passes because `sender` = router. The pool admin's access-control boundary is bypassed by an unprivileged path, matching the "admin-boundary break" allowed impact category.

### Likelihood Explanation

The `MetricOmmSimpleRouter` is the canonical swap entry point documented for end users. Any pool that deploys `SwapAllowlistExtension` to restrict swaps to a curated set of participants will encounter this immediately when those participants attempt to use the router. The pool admin's natural remediation (allowlisting the router) triggers Scenario B. No special permissions, flash loans, or exotic token behavior are required — a standard router call suffices.

### Recommendation

The extension must check the economically relevant actor, not the immediate caller. Two sound approaches:

1. **Pass the originating user through the router.** Add a `swapperOverride` field to `extensionData` that the router populates with `msg.sender` before calling `pool.swap()`. The extension reads and verifies this field, checking the override address against the allowlist. The pool must validate that the override is consistent with the callback payer stored in transient storage.

2. **Check `sender` only when it is not a known router.** Maintain a factory-level registry of trusted routers; when `sender` is a trusted router, extract the originating user from a signed or transient-storage-backed context rather than from `sender` directly.

### Proof of Concept

```
Setup:
  pool configured with SwapAllowlistExtension
  allowedSwapper[pool][alice] = true   // alice is the intended user
  router is NOT allowlisted

Step 1 – Allowlisted user blocked via router:
  alice calls MetricOmmSimpleRouter.exactInputSingle({pool: pool, ...})
  → router calls pool.swap(recipient, ...) with msg.sender = router
  → _beforeSwap(sender=router, ...)
  → SwapAllowlistExtension: allowedSwapper[pool][router] == false → revert NotAllowedToSwap
  alice cannot swap despite being individually allowlisted.

Step 2 – Admin allowlists router to fix alice's problem:
  admin calls setAllowedToSwap(pool, router, true)

Step 3 – Unprivileged bypass:
  bob (not allowlisted) calls MetricOmmSimpleRouter.exactInputSingle({pool: pool, ...})
  → router calls pool.swap(recipient, ...) with msg.sender = router
  → _beforeSwap(sender=router, ...)
  → SwapAllowlistExtension: allowedSwapper[pool][router] == true → passes
  bob swaps successfully, allowlist fully bypassed.
```

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

**File:** metric-periphery/contracts/extensions/SwapAllowlistExtension.sol (L12-19)
```text
  mapping(address pool => mapping(address swapper => bool)) public allowedSwapper;
  mapping(address pool => bool) public allowAllSwappers;

  constructor(address factory_) BaseMetricExtension(factory_) {}

  function setAllowedToSwap(address pool_, address swapper, bool allowed) external onlyPoolAdmin(pool_) {
    allowedSwapper[pool_][swapper] = allowed;
    emit AllowedToSwapSet(pool_, swapper, allowed);
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
