### Title
`SwapAllowlistExtension` Checks Router Address Instead of End-User, Allowing Any User to Bypass the Swap Allowlist — (`metric-periphery/contracts/extensions/SwapAllowlistExtension.sol`)

---

### Summary

`SwapAllowlistExtension.beforeSwap` gates swaps by checking the `sender` argument, which the pool sets to `msg.sender` of `pool.swap()`. When a user routes through `MetricOmmSimpleRouter`, `msg.sender` of `pool.swap()` is the router, not the end-user. If the pool admin allowlists the router address (the only way to permit router-mediated swaps), every user — including those the admin explicitly excluded — can bypass the allowlist by calling the public router.

---

### Finding Description

`MetricOmmPool.swap()` passes its own `msg.sender` as the `sender` argument to `_beforeSwap`: [1](#0-0) 

`ExtensionCalling._beforeSwap` forwards that value unchanged to every configured extension: [2](#0-1) 

`SwapAllowlistExtension.beforeSwap` then checks `allowedSwapper[msg.sender /*pool*/][sender /*direct caller of pool.swap*/]`: [3](#0-2) 

When a user calls `MetricOmmSimpleRouter.exactInputSingle()`, the router becomes `msg.sender` of `pool.swap()`. The extension therefore checks `allowedSwapper[pool][router]`, not `allowedSwapper[pool][end-user]`. A pool admin who wants router-mediated swaps to work for any allowlisted user must add the router to the allowlist. Once the router is allowlisted, the check passes for every caller of the router, regardless of whether that caller is individually permitted.

The `DepositAllowlistExtension` does not share this flaw because it checks the `owner` parameter (the position beneficiary), not the direct caller: [4](#0-3) 

---

### Impact Explanation

A pool configured with `SwapAllowlistExtension` to restrict trading to a curated set of addresses (e.g., KYC-verified counterparties, protocol-owned addresses, or specific market makers) can be freely traded against by any user who routes through the public `MetricOmmSimpleRouter`. This breaks the admin-boundary invariant: the pool admin's access-control configuration is silently nullified for all router-mediated swaps. Unauthorized swaps can drain LP value, move the oracle-anchored price within the allowed band, and interact with downstream extensions (e.g., `OracleValueStopLossExtension`, `PriceVelocityGuardExtension`) in ways the admin did not intend.

---

### Likelihood Explanation

The `MetricOmmSimpleRouter` is the primary user-facing entry point documented in the protocol. Any pool admin who deploys a swap-allowlisted pool and also wants legitimate allowlisted users to trade via the router must add the router to the allowlist — this is the only viable configuration. That single admin action, which is expected and necessary for normal operation, simultaneously opens the bypass to all users. No attacker capability beyond calling a public function is required.

---

### Recommendation

Pass the original end-user identity through the swap path so the extension can gate on it. Two concrete options:

1. **Router-side**: Have `MetricOmmSimpleRouter` encode the original `msg.sender` into `extensionData` and have `SwapAllowlistExtension.beforeSwap` decode and check that value when present.
2. **Pool-side**: Add an explicit `originator` field to the `swap()` call signature (similar to ERC-4337 or Uniswap v4's `hookData` pattern) so the pool can forward the true initiator to extensions without relying on `msg.sender`.

Either way, the extension must verify the end-user identity, not the intermediary contract identity.

---

### Proof of Concept

```
Setup:
  pool configured with SwapAllowlistExtension
  pool admin calls setAllowedToSwap(pool, router, true)   // to enable router swaps
  pool admin does NOT call setAllowedToSwap(pool, attacker, true)

Attack:
  attacker calls MetricOmmSimpleRouter.exactInputSingle(pool, ...)
    → router calls pool.swap(recipient=attacker, ...)
      → msg.sender of pool.swap() = router
      → _beforeSwap(sender=router, ...)
      → SwapAllowlistExtension.beforeSwap(sender=router, ...)
        → allowedSwapper[pool][router] == true  ✓
        → swap proceeds for attacker
```

The attacker successfully swaps on a pool that was configured to exclude them, with no privileged access required beyond calling the public router. [3](#0-2) [5](#0-4)

### Citations

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

**File:** metric-core/contracts/ExtensionCalling.sol (L151-177)
```text
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
