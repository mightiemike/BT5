Audit Report

## Title
`SwapAllowlistExtension.beforeSwap` gates the router address instead of the end user, allowing any user to bypass the swap allowlist via `MetricOmmSimpleRouter` — (File: `metric-periphery/contracts/extensions/SwapAllowlistExtension.sol`)

## Summary
`SwapAllowlistExtension.beforeSwap` checks the `sender` argument forwarded by the pool, which is `msg.sender` of the `pool.swap()` call. When a user routes through `MetricOmmSimpleRouter`, that `msg.sender` is the router contract, not the end user. If the pool admin allowlists the router — the natural operational step to enable standard periphery UX — every user, including those not individually permitted, can bypass the guard by routing through the router.

## Finding Description
`MetricOmmPool.swap` passes `msg.sender` as the `sender` argument to `_beforeSwap`: [1](#0-0) 

`SwapAllowlistExtension.beforeSwap` then checks whether that `sender` is on the allowlist for the calling pool (`msg.sender` inside the extension is the pool): [2](#0-1) 

`MetricOmmSimpleRouter.exactInputSingle` calls `pool.swap()` directly, making the router the `msg.sender` seen by the pool: [3](#0-2) 

The same pattern holds for `exactInput`, `exactOutputSingle`, and `exactOutput` — in every case the router is the direct caller of `pool.swap()`: [4](#0-3) 

Because the allowlist is keyed on `sender` (the router address), the admin must call `setAllowedToSwap(pool, router, true)` for any router-mediated swap to succeed. Once the router is allowlisted, the check `allowedSwapper[pool][sender]` passes for every user who routes through it, regardless of whether that user is individually permitted. The extension has no mechanism to recover the original end-user identity; it only sees the `sender` argument the pool supplies. [5](#0-4) 

## Impact Explanation
A pool configured with `SwapAllowlistExtension` to restrict swaps to a specific set of counterparties (e.g., KYC'd addresses, institutional partners) is fully bypassed the moment the router is allowlisted. Any unpermissioned user can call `MetricOmmSimpleRouter.exactInputSingle` and execute swaps in the restricted pool, receiving tokens at oracle-anchored prices the LP intended to offer only to approved parties. This constitutes a direct loss of LP principal and a broken core pool invariant (the allowlist guard), meeting the "admin-boundary break" and "broken core pool functionality causing loss of funds" impact criteria.

## Likelihood Explanation
Allowlisting the router is the expected operational step for any pool that wants to support standard periphery UX while also restricting the participant set. The admin has no indication from the contract or its documentation that allowlisting the router opens the gate to all users. The bypass requires only a standard `exactInputSingle` call — no special privileges, no flash loans, no contract deployment. Any unpermissioned user who is aware of the pool can exploit this.

## Recommendation
The `beforeSwap` hook should gate the end user, not the immediate caller. Two sound approaches:

1. **Pass the payer through `extensionData`**: The router already stores the payer in transient storage (`_getPayer()`). It can encode the payer address into `extensionData` so the extension can verify it. The extension must verify that `msg.sender` (the pool) is a legitimate pool before trusting the encoded identity.

2. **Restrict to direct callers only**: Disallow router-mediated swaps for allowlisted pools, or redesign the hook interface to carry the originating user address as a first-class parameter.

Note: `DepositAllowlistExtension` does not share this flaw because it gates `owner` (the position owner), which is the economically relevant identity for deposits and is passed explicitly by the liquidity adder.

## Proof of Concept
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
