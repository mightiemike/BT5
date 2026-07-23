### Title
`SwapAllowlistExtension` gates the router address instead of the actual user, allowing any user to bypass the swap allowlist via `MetricOmmSimpleRouter` — (File: `metric-periphery/contracts/extensions/SwapAllowlistExtension.sol`)

---

### Summary

`SwapAllowlistExtension.beforeSwap` checks the `sender` argument forwarded by the pool, which is `msg.sender` of the `pool.swap()` call. When a user routes through `MetricOmmSimpleRouter`, that `msg.sender` is the router contract, not the end user. If the pool admin allowlists the router (the natural action to enable standard UX), every user — including those not on the allowlist — can bypass the guard by going through the router.

---

### Finding Description

`MetricOmmPool.swap` passes `msg.sender` as the `sender` argument to `_beforeSwap`: [1](#0-0) 

`ExtensionCalling._beforeSwap` forwards that value unchanged to every configured extension: [2](#0-1) 

`SwapAllowlistExtension.beforeSwap` then checks whether that `sender` is on the allowlist for the calling pool: [3](#0-2) 

`MetricOmmSimpleRouter.exactInputSingle` calls `pool.swap()` directly, making the router the `msg.sender` seen by the pool: [4](#0-3) 

The same pattern holds for `exactInput`, `exactOutputSingle`, and `exactOutput` — in every case the router is the direct caller of `pool.swap()`. [5](#0-4) 

Because the allowlist is keyed on `sender` (the router), the admin must add the router to `allowedSwapper[pool][router]` for any router-mediated swap to succeed. Once the router is allowlisted, the check `allowedSwapper[pool][sender]` passes for **every** user who routes through it, regardless of whether that user is individually permitted.

The `SwapAllowlistExtension` has no mechanism to recover the original end-user identity from the router; it only sees the `sender` argument the pool supplies. [6](#0-5) 

---

### Impact Explanation

A pool configured with `SwapAllowlistExtension` to restrict swaps to a specific set of counterparties (e.g., KYC'd addresses, institutional partners) is fully bypassed the moment the router is allowlisted. Any unpermissioned user can call `MetricOmmSimpleRouter.exactInputSingle` and execute swaps in the restricted pool, draining liquidity at oracle-anchored prices that the LP intended to offer only to approved parties. This is a direct loss of LP principal and a broken core pool invariant (the allowlist guard).

---

### Likelihood Explanation

The likelihood is **medium-high**. Allowlisting the router is the expected operational step for any pool that wants to support standard periphery UX while also restricting the participant set. The admin has no indication from the contract or its documentation that allowlisting the router opens the gate to all users. The bypass requires only a standard `exactInputSingle` call — no special privileges, no flash loans, no contract deployment.

---

### Recommendation

The `beforeSwap` hook should gate the **end user**, not the immediate caller. Two sound approaches:

1. **Pass the payer through `extensionData`**: The router already stores the payer in transient storage (`_getPayer()`). It can encode the payer address into `extensionData` so the extension can verify it. The extension must then verify that `msg.sender` (the pool) is a legitimate pool before trusting the encoded identity.

2. **Check `recipient` instead of `sender` for router flows, or require direct calls**: Restrict the allowlist to direct `pool.swap()` callers only (no router support), or redesign the hook interface to carry the originating user address as a first-class parameter.

The `DepositAllowlistExtension` does not share this flaw because it gates `owner` (the position owner), which the liquidity adder passes explicitly and which the pool records for LP accounting — the economically relevant identity for deposits. [7](#0-6) 

---

### Proof of Concept

```
Setup:
  pool configured with SwapAllowlistExtension
  admin calls setAllowedToSwap(pool, router, true)   // enable standard UX
  admin calls setAllowedToSwap(pool, alice, true)    // intended allowlist
  bob is NOT on the allowlist

Attack:
  bob calls MetricOmmSimpleRouter.exactInputSingle({pool: pool, ...})
    → router calls pool.swap(recipient=bob, ...)
    → pool calls _beforeSwap(sender=router, ...)
    → SwapAllowlistExtension.beforeSwap(sender=router, ...)
    → allowedSwapper[pool][router] == true  ✓ passes
    → swap executes; bob receives tokens from the restricted pool

Result:
  bob, a non-allowlisted user, successfully swaps in a pool
  the LP intended to restrict to approved counterparties only.
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
