### Title
SwapAllowlistExtension Checks Router Address Instead of Actual Swapper, Enabling Full Allowlist Bypass via Router - (File: metric-periphery/contracts/extensions/SwapAllowlistExtension.sol)

### Summary
`SwapAllowlistExtension.beforeSwap` gates swaps on `sender`, which the pool sets to its own `msg.sender`. When `MetricOmmSimpleRouter` is the caller, `sender` = router address, not the end user. If the pool admin allowlists the router (a natural step to enable multi-hop flows), every user on-chain can bypass the per-address restriction by routing through it.

### Finding Description
`MetricOmmPool.swap` passes its own `msg.sender` as the `sender` argument to every configured extension: [1](#0-0) 

`SwapAllowlistExtension.beforeSwap` then evaluates that value against the per-pool allowlist: [2](#0-1) 

`msg.sender` inside the extension is the pool; `sender` is whoever called `pool.swap()`. When `MetricOmmSimpleRouter.exactInputSingle` (or `exactInput`) is used, the router is the direct caller of `pool.swap()`: [3](#0-2) 

So the allowlist lookup becomes `allowedSwapper[pool][router]`, not `allowedSwapper[pool][end_user]`. The extension's public view helper `isAllowedToSwap(pool, swapper)` also uses the same mapping: [4](#0-3) 

This creates a two-sided failure:

1. **Authorized users are silently blocked** when they call through the router, because their individual address is allowlisted but the router's address is not.
2. **All users are silently unblocked** the moment the pool admin adds the router to the allowlist (the natural fix for problem 1), because `allowedSwapper[pool][router] = true` satisfies the check for every caller who routes through it.

The `DepositAllowlistExtension` avoids this specific path because it checks `owner` (the position owner parameter, not `sender`), but the swap guard is fully exposed. [5](#0-4) 

### Impact Explanation
A pool configured with `SwapAllowlistExtension` to restrict trading to a curated set of addresses (e.g., KYC-verified counterparties, institutional desks, or whitelisted market makers) loses that restriction entirely for any user who calls through `MetricOmmSimpleRouter`. Once the router is allowlisted — which is the only way to restore router-based swaps for legitimate users — the guard is permanently open to the public. Unauthorized traders gain access to pool liquidity at the oracle-anchored bid/ask, which may be priced more favorably than open-market venues, extracting value from LPs and defeating the pool admin's access-control intent. This is an admin-boundary break: an unprivileged path (the router) bypasses a configured access guard.

### Likelihood Explanation
Medium. The router is the primary user-facing entry point for multi-hop and single-hop swaps. Any pool that uses `SwapAllowlistExtension` and also wants to support router-based flows will encounter the blocked-user problem immediately and will be tempted to allowlist the router as the obvious fix. The bypass then becomes active for all users without any further action.

### Recommendation
The extension must identify the true end-user, not the intermediary. Two sound approaches:

1. **Pass the originating user through `extensionData`**: the router encodes `msg.sender` into `extensionData`; the extension decodes and verifies it. The pool admin allowlists end-user addresses as today; the router is never added to the allowlist.
2. **Check `recipient` instead of (or in addition to) `sender`**: for single-hop swaps the recipient is often the end-user. For multi-hop this still requires the router to forward the originator.

Either way, the extension's `beforeSwap` signature already receives both `sender` and `recipient`; the fix is a matter of which field (or decoded `extensionData` field) is used for the allowlist lookup.

### Proof of Concept

```
Setup:
  pool P configured with SwapAllowlistExtension E
  allowedSwapper[P][alice] = true   // alice is the only authorized trader
  allowedSwapper[P][router] = false // router not yet listed

Step 1 – alice tries to swap via router:
  alice → router.exactInputSingle(pool=P, ...)
  router → P.swap(recipient=alice, ...)   // msg.sender = router
  P → E.beforeSwap(sender=router, ...)
  check: allowedSwapper[P][router] == false  → revert NotAllowedToSwap
  // alice is blocked despite being individually authorized

Step 2 – admin adds router to fix alice's problem:
  admin: setAllowedToSwap(P, router, true)

Step 3 – bob (not authorized) bypasses the guard:
  bob → router.exactInputSingle(pool=P, ...)
  router → P.swap(recipient=bob, ...)    // msg.sender = router
  P → E.beforeSwap(sender=router, ...)
  check: allowedSwapper[P][router] == true  → passes
  // bob swaps successfully; allowlist is fully bypassed
``` [6](#0-5) [1](#0-0) [3](#0-2)

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

**File:** metric-periphery/contracts/extensions/SwapAllowlistExtension.sol (L11-42)
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
}
```

**File:** metric-periphery/contracts/MetricOmmSimpleRouter.sol (L103-112)
```text
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
