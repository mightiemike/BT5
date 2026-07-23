Looking at the swap allowlist extension and how the router passes `sender` to the pool hook, I need to trace the exact actor binding through the full call path.

**Key code path:**

`MetricOmmPool.swap` passes `msg.sender` as `sender` to `_beforeSwap`: [1](#0-0) 

`ExtensionCalling._beforeSwap` forwards that `sender` verbatim to the extension: [2](#0-1) 

`SwapAllowlistExtension.beforeSwap` gates on `allowedSwapper[msg.sender][sender]` — where `msg.sender` = pool and `sender` = whoever called `pool.swap()`: [3](#0-2) 

`MetricOmmSimpleRouter.exactInputSingle` calls `pool.swap()` directly — so from the pool's perspective `msg.sender` = router, not the end user: [4](#0-3) 

The same pattern holds for `exactInput`, `exactOutputSingle`, and `exactOutput`. [5](#0-4) 

The `onlyPoolAdmin` modifier on the setter correctly gates admin writes, but the hook's runtime check is the wrong identity: [6](#0-5) 

---

### Title
SwapAllowlistExtension Gates Router Address Instead of End User, Enabling Full Allowlist Bypass via MetricOmmSimpleRouter — (`metric-periphery/contracts/extensions/SwapAllowlistExtension.sol`)

### Summary
`SwapAllowlistExtension.beforeSwap` checks `allowedSwapper[pool][sender]` where `sender` is the immediate caller of `pool.swap()`. When any swap is routed through `MetricOmmSimpleRouter`, `sender` = router address. If the pool admin allowlists the router (the only way to let allowlisted users trade through the router), every non-allowlisted user can bypass the curated-pool restriction by routing through the same public contract.

### Finding Description
The pool's `swap` function passes `msg.sender` — the direct caller — as the `sender` argument to `_beforeSwap`, which forwards it unchanged to every configured extension. `SwapAllowlistExtension.beforeSwap` then evaluates:

```solidity
if (!allowAllSwappers[msg.sender] && !allowedSwapper[msg.sender][sender]) {
    revert IMetricOmmPoolActions.NotAllowedToSwap();
}
```

`msg.sender` here is the pool (correct), but `sender` is the router when the user enters through `MetricOmmSimpleRouter`. The allowlist therefore checks whether the **router** is permitted, not whether the **end user** is permitted.

This forces the pool admin into an impossible choice:

| Admin decision | Effect on allowlisted users | Effect on non-allowlisted users |
|---|---|---|
| Do **not** allowlist the router | Cannot use the router at all | Correctly blocked |
| **Allowlist the router** | Can use the router | **Also bypass the allowlist via the router** |

The second row is the vulnerability: allowlisting the router — a natural operational step — silently opens the gate to every address.

### Impact Explanation
Any non-allowlisted address can trade on a pool that the admin intended to restrict to a curated set of counterparties. The allowlist guard — the sole access-control mechanism on the swap path for such pools — is rendered ineffective. LPs who deposited under the assumption that only vetted counterparties could trade against them are exposed to unrestricted swap flow, which can include adversarial MEV, front-running, or simply unauthorized volume. This is a direct admin-boundary break: the pool admin's configured policy is bypassed by an unprivileged path (the public router).

### Likelihood Explanation
Medium. The bypass requires the pool admin to have allowlisted the router address. Any pool that wants allowlisted users to access the router must do exactly this, making the precondition a routine operational step rather than an exotic configuration. Once the router is allowlisted, the bypass is trivially reachable by any address with no special privileges.

### Recommendation
The `SwapAllowlistExtension` must gate on the **original end user**, not the immediate caller. Two viable approaches:

1. **Pass the real user through `extensionData`**: The router encodes `msg.sender` into `extensionData`; the extension decodes and checks it. This requires a coordinated change to the router and the extension.
2. **Check `tx.origin` as a fallback**: When `sender` is a known router, fall back to `tx.origin`. This is simpler but introduces `tx.origin` risks in other contexts.

The cleanest fix is approach 1: the router should encode the originating user address in `extensionData`, and `SwapAllowlistExtension.beforeSwap` should decode and check that address when `sender` is a registered router.

### Proof of Concept
```
Setup:
  pool deployed with SwapAllowlistExtension
  pool admin calls setAllowedToSwap(pool, userA, true)
  pool admin calls setAllowedToSwap(pool, router, true)   ← needed for userA to use the router

Attack:
  userB (not allowlisted) calls:
    router.exactInputSingle({ pool: pool, ... })

Execution trace:
  router.exactInputSingle()
    → pool.swap(recipient, ...)          // msg.sender in pool = router
      → _beforeSwap(sender=router, ...)
        → SwapAllowlistExtension.beforeSwap(sender=router, ...)
          → allowedSwapper[pool][router] == true  ✓
          → returns selector (no revert)
      → swap executes for userB
```

`userB`'s swap succeeds despite never being added to the allowlist, because the extension evaluated the router's allowlist entry rather than `userB`'s.

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

**File:** metric-periphery/contracts/extensions/base/BaseMetricExtension.sol (L31-35)
```text
  modifier onlyPoolAdmin(address pool_) {
    address poolAdmin = IMetricOmmPoolFactory(FACTORY).poolAdmin(pool_);
    if (msg.sender != poolAdmin) revert OnlyPoolAdmin(pool_, msg.sender, poolAdmin);
    _;
  }
```
