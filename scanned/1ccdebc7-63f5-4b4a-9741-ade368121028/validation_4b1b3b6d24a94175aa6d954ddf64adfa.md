### Title
SwapAllowlistExtension Checks Router Address Instead of User Identity, Allowing Any User to Bypass the Swap Allowlist via MetricOmmSimpleRouter — (`metric-periphery/contracts/extensions/SwapAllowlistExtension.sol`)

### Summary

`SwapAllowlistExtension.beforeSwap` gates swaps by checking the `sender` argument, which the pool sets to `msg.sender` — the direct caller of `pool.swap()`. When users route through `MetricOmmSimpleRouter`, the pool's `msg.sender` is the router contract, not the end user. The extension therefore checks whether the **router** is allowlisted, not whether the **user** is allowlisted. Any unprivileged user can bypass a per-user swap allowlist by calling the public router instead of the pool directly.

### Finding Description

`MetricOmmPool.swap` passes `msg.sender` as the `sender` argument to `_beforeSwap`: [1](#0-0) 

`ExtensionCalling._beforeSwap` forwards that value unchanged as the first argument to every configured extension: [2](#0-1) 

`SwapAllowlistExtension.beforeSwap` then checks `allowedSwapper[msg.sender][sender]`, where `msg.sender` is the pool and `sender` is whoever called `pool.swap()`: [3](#0-2) 

`MetricOmmSimpleRouter.exactInputSingle` calls `pool.swap(...)` directly, making the router the pool's `msg.sender`: [4](#0-3) 

The same pattern holds for `exactInput` (all hops), `exactOutputSingle`, and `exactOutput` — in every case the router is the direct caller of `pool.swap()`. [5](#0-4) 

**Result**: the extension evaluates `allowedSwapper[pool][router]`, not `allowedSwapper[pool][user]`. Two broken outcomes follow:

1. **Allowlist bypass** — if the pool admin allowlists the router (to let allowlisted users trade via the router), every unprivileged user can also swap through the router and bypass the per-user restriction entirely.
2. **Allowlist over-restriction** — if the pool admin does not allowlist the router, even explicitly allowlisted users are blocked from using the router, breaking the supported periphery path.

Neither outcome matches the invariant stated in the extension's own NatSpec: *"Gates `swap` by swapper address, per pool."* [6](#0-5) 

### Impact Explanation

A pool that deploys `SwapAllowlistExtension` to restrict trading to specific counterparties (e.g., KYC-verified market makers, institutional partners, or whitelisted arbitrageurs) loses that protection entirely for router-mediated swaps. Any unprivileged user can call `MetricOmmSimpleRouter.exactInputSingle` and trade against the restricted pool's LP positions. LP funds are exposed to unauthorized toxic flow that the allowlist was configured to prevent, constituting a direct loss of LP principal and owed fees above the contest threshold.

### Likelihood Explanation

The `MetricOmmSimpleRouter` is a public, permissionless contract. No special role, token balance, or prior state is required. Any user who observes that a pool has a swap allowlist can trivially route through the router instead of calling the pool directly. The bypass is reachable in a single transaction.

### Recommendation

The extension must resolve the original user rather than the direct pool caller. Two viable approaches:

1. **Pass the original user through the router** — have the router encode the original `msg.sender` into `extensionData` and have the extension decode and check that value. This requires a coordinated change to both the router and the extension.

2. **Check `sender` against a router-aware allowlist** — extend the extension to maintain a separate mapping of trusted routers and, when `sender` is a trusted router, extract and verify the end-user identity from `extensionData`.

The simplest safe default is to treat any unrecognized `sender` (i.e., one that is not directly allowlisted) as unauthorized, and require that the router explicitly forwards the original user identity in `extensionData` for the extension to verify.

### Proof of Concept

```
Setup:
  - Deploy pool with SwapAllowlistExtension configured
  - Pool admin calls setAllowedToSwap(pool, router, true)
    (necessary so allowlisted users can trade via router)
  - Pool admin calls setAllowedToSwap(pool, alice, true)
    (alice is the only intended user)
  - Pool admin does NOT call setAllowedToSwap(pool, bob, true)
    (bob is an unauthorized user)

Attack:
  - bob calls MetricOmmSimpleRouter.exactInputSingle({pool: pool, ...})
  - Router calls pool.swap(recipient, zeroForOne, amount, ...)
  - Pool calls extension.beforeSwap(router, recipient, ...)
  - Extension checks allowedSwapper[pool][router] == true  ✓
  - Swap executes — bob trades against LP funds he was never authorized to access

Expected: revert NotAllowedToSwap
Actual:   swap succeeds; bob bypasses the allowlist
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

**File:** metric-core/contracts/ExtensionCalling.sol (L160-177)
```text
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
