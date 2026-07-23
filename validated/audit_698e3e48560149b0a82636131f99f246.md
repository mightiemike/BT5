### Title
SwapAllowlistExtension Checks Router Address Instead of End-User, Allowing Allowlist Bypass via MetricOmmSimpleRouter - (File: metric-periphery/contracts/extensions/SwapAllowlistExtension.sol)

### Summary

`SwapAllowlistExtension.beforeSwap` gates swaps by checking `sender`, which is `msg.sender` of the `pool.swap()` call. When a user routes through `MetricOmmSimpleRouter`, `sender` resolves to the router contract address, not the actual end-user. If the pool admin allowlists the router (a necessary step for any user to swap through it), every user — including those not individually allowlisted — can bypass the per-user swap guard.

### Finding Description

`SwapAllowlistExtension.beforeSwap` receives `sender` as its first argument and checks it against the per-pool allowlist:

```solidity
// SwapAllowlistExtension.sol line 37
if (!allowAllSwappers[msg.sender] && !allowedSwapper[msg.sender][sender]) {
    revert IMetricOmmPoolActions.NotAllowedToSwap();
}
``` [1](#0-0) 

The pool's `swap` function passes `msg.sender` as `sender` to `_beforeSwap`:

```solidity
// MetricOmmPool.sol line 230-240
_beforeSwap(
    msg.sender,   // ← this becomes `sender` in the extension
    recipient,
    ...
);
``` [2](#0-1) 

When a user calls `MetricOmmSimpleRouter.exactInputSingle(pool, ...)`, the router calls `pool.swap(...)` directly, making `msg.sender` of the pool call equal to the **router address**, not the end-user. The extension therefore evaluates `allowedSwapper[pool][router]` — not `allowedSwapper[pool][endUser]`.

The attack path:

1. Pool admin deploys a curated pool with `SwapAllowlistExtension` to restrict swaps to KYC'd counterparties.
2. Admin allowlists individual users A and B directly.
3. Admin also allowlists the `MetricOmmSimpleRouter` address so that allowlisted users can use the standard periphery router.
4. Unauthorized user C calls `MetricOmmSimpleRouter.exactInputSingle(pool, ...)`.
5. The router calls `pool.swap(recipient=C, ...)` with `msg.sender = router`.
6. `beforeSwap` checks `allowedSwapper[pool][router]` → `true` → guard passes.
7. User C executes a swap on the curated pool despite not being individually allowlisted. [1](#0-0) 

### Impact Explanation

The swap allowlist guard is silently bypassed for any user who routes through the router once the router address is allowlisted. The pool admin cannot simultaneously (a) allow allowlisted users to use the router and (b) block non-allowlisted users from using the same router. Any non-allowlisted user can trade on a curated pool, defeating compliance, counterparty-restriction, or rate-limiting policies. Depending on pool design, this can result in unauthorized extraction of LP assets or fee revenue from a pool intended for restricted participants.

### Likelihood Explanation

The router is the standard, documented periphery swap path. A pool admin who wants allowlisted users to be able to use the router must allowlist the router address. This is a natural operational step, not an exotic misconfiguration. Any user aware of the router address can exploit the bypass without any privileged access.

### Recommendation

The extension must gate on the actual economic actor, not the intermediary. Two viable approaches:

1. **Caller-forwarding via `extensionData`**: Require the router to encode the originating user address in `extensionData` and have `SwapAllowlistExtension` decode and check that address instead of `sender`. The extension should revert if `sender` is a known router and `extensionData` does not contain a valid allowlisted address.

2. **Direct-call-only policy**: Document that `SwapAllowlistExtension` is incompatible with router-mediated swaps and add an on-chain guard that reverts when `sender` is not an EOA or is a known intermediary contract.

The `DepositAllowlistExtension` does not share this flaw because it checks `owner` (the position beneficiary), which the liquidity adder validates equals `msg.sender` via `_validateOwner`. [3](#0-2) 

### Proof of Concept

```solidity
// Assume:
// - pool has SwapAllowlistExtension configured as beforeSwap hook
// - admin has allowlisted router: extension.setAllowedToSwap(pool, address(router), true)
// - userC is NOT individually allowlisted

// userC calls the router directly:
router.exactInputSingle(
    pool,
    false,          // zeroForOne
    int128(1000),
    type(uint128).max,
    userC,          // recipient
    ""
);
// The router calls pool.swap(recipient=userC, ...) with msg.sender=router
// Extension checks allowedSwapper[pool][router] == true → passes
// userC receives output tokens despite not being on the allowlist
```

The `beforeSwap` hook receives `sender = address(router)`, checks `allowedSwapper[pool][router]` which is `true`, and allows the swap to proceed. [1](#0-0) [4](#0-3) [5](#0-4)

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

**File:** metric-core/contracts/MetricOmmPool.sol (L217-240)
```text
  function swap(
    address recipient,
    bool zeroForOne,
    int128 amountSpecified,
    uint128 priceLimitX64,
    bytes calldata callbackData,
    bytes calldata extensionData
  ) external whenNotPaused nonReentrant(PoolActions.SWAP) returns (int128, int128) {
    require(amountSpecified != 0, InvalidAmount());

    uint256 packedSlot0Initial = Slot0Library.loadPackedSlot0();
    (uint128 bidPriceX64, uint128 askPriceX64) = _getBidAndAskPriceX64();

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
