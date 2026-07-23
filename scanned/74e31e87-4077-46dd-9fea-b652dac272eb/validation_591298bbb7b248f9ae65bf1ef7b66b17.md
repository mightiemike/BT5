### Title
`SwapAllowlistExtension` Checks Router Address as Swapper Identity, Allowing Any User to Bypass Swap Allowlist on Curated Pools — (File: `metric-periphery/contracts/extensions/SwapAllowlistExtension.sol`)

---

### Summary

The `SwapAllowlistExtension.beforeSwap` hook gates swaps by checking `sender` — the `msg.sender` of `MetricOmmPool.swap` — against a per-pool allowlist keyed by `(pool, sender)`. When a user routes through the public `MetricOmmSimpleRouter`, the pool's `msg.sender` is the router contract, not the actual user. Because the router is a permissionless public contract, any user can present the router's address as the "swapper" identity. If the router is allowlisted (a necessary condition for any legitimate user to use the router on a curated pool), the per-user allowlist is completely bypassed.

---

### Finding Description

`MetricOmmPool.swap` passes `msg.sender` as `sender` to `_beforeSwap`: [1](#0-0) 

`ExtensionCalling._beforeSwap` then encodes and forwards that `sender` verbatim to every configured extension: [2](#0-1) 

`SwapAllowlistExtension.beforeSwap` checks `sender` against the allowlist keyed by `(pool, sender)`. The research scan confirms the lookup is "keyed by pool and sender."

When a user calls `MetricOmmSimpleRouter.exactInput(...)` (or any `exact*` variant), the router calls `pool.swap(...)` directly. At that point `msg.sender` inside the pool — and therefore `sender` delivered to the extension — is the **router contract address**, not the originating user.

For any legitimate user to use the router on a curated pool, the pool admin must add the router to the allowlist. Once the router is allowlisted, **any** user — including those explicitly excluded — can route through the public router and have their swap pass the allowlist check, because the extension sees the router address, not the actual user.

The `addLiquidityWeighted` probe path in `MetricOmmPoolLiquidityAdder` creates an analogous issue for the deposit allowlist: the probe call triggers `_beforeAddLiquidity` with `sender = liquidity_adder`, and the allowlist (keyed by `owner`) is checked against the position owner supplied by the caller, not the payer. A disallowed payer can supply an allowed `owner` address to pass the check while still funding the deposit. [3](#0-2) [4](#0-3) 

---

### Impact Explanation

A curated pool with a swap allowlist (e.g., a KYC-gated pool, a private institutional pool, or a pool restricted to specific market makers) can be accessed by any unprivileged user by routing through `MetricOmmSimpleRouter`. The allowlist guard is completely bypassed. Unauthorized users can execute swaps against the pool's liquidity, potentially:

- Extracting value from LPs who expected a restricted counterparty set.
- Disrupting the pool's intended pricing dynamics or fee structure.
- Circumventing regulatory or compliance controls the pool admin intended to enforce.

This is a **direct loss of policy enforcement** with fund-impacting consequences for LPs on curated pools.

---

### Likelihood Explanation

`MetricOmmSimpleRouter` is a public, permissionless periphery contract with no access controls on its `exact*` entry points. Any user can call it. The bypass requires only that the router be in the allowlist — which is a **necessary** condition for any legitimate user to use the router on a curated pool. Pool admins who configure a swap allowlist and also want to support router-mediated swaps will inevitably create this bypass condition. The attack requires no special privileges, no malicious setup, and no non-standard tokens. [5](#0-4) 

---

### Recommendation

The `SwapAllowlistExtension` must not check `sender` (the immediate caller of `pool.swap`) as the authoritative swapper identity when the pool supports router-mediated swaps. Options:

1. **Forward original caller in `extensionData`**: Have the router encode the original `msg.sender` into `extensionData` and have the extension decode and verify it (e.g., via a router-signed attestation). The extension then checks the decoded user address, not `sender`.
2. **Check `recipient` instead of `sender`**: If the pool's design guarantees that `recipient` is always the actual beneficiary, check `recipient`. This is weaker but avoids the router indirection problem.
3. **Disallow router on allowlisted pools**: Document and enforce that `MetricOmmSimpleRouter` cannot be used with pools that have a `SwapAllowlistExtension` configured in restricted mode, and add a runtime check in the router.

The analogous fix for `DepositAllowlistExtension` is to check `sender` (the payer) rather than `owner` when the intent is to restrict who can fund deposits, or to explicitly document that the allowlist gates position ownership only and that any payer may fund an allowed owner's position.

---

### Proof of Concept

1. Pool admin deploys a pool with `SwapAllowlistExtension` configured in restricted mode. Admin adds `alice` and `MetricOmmSimpleRouter` to the allowlist (router must be added for `alice` to use the router).
2. `bob` (not in the allowlist) calls `MetricOmmSimpleRouter.exactInput(...)` targeting the curated pool.
3. The router calls `pool.swap(recipient=bob, ...)` with `msg.sender = router`.
4. `MetricOmmPool.swap` passes `sender = router` to `_beforeSwap`. [6](#0-5) 

5. `SwapAllowlistExtension.beforeSwap` checks `sender = router` against the allowlist — the router is allowlisted, so the check passes.
6. `bob`'s swap executes successfully despite `bob` not being in the allowlist.
7. `bob` receives output tokens; the curated pool's LP set has been exposed to an unauthorized counterparty. [7](#0-6)

### Citations

**File:** metric-core/contracts/MetricOmmPool.sol (L217-245)
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

    (uint256 midPriceX64, uint256 baseFeeX64) =
      SwapMath.midAndSpreadFeeX64FromBidAsk(uint256(bidPriceX64), uint256(askPriceX64));
    SwapMath.InternalSwapParams memory params =
      SwapMath.InternalSwapParams({midPriceX64: midPriceX64, baseFeeX64: baseFeeX64, priceLimitX64: priceLimitX64});
```

**File:** metric-core/contracts/ExtensionCalling.sol (L88-99)
```text
  function _beforeAddLiquidity(
    address sender,
    address owner,
    uint80 salt,
    LiquidityDelta calldata deltas,
    bytes calldata extensionData
  ) internal {
    _callExtensionsInOrder(
      BEFORE_ADD_LIQUIDITY_ORDER,
      abi.encodeCall(IMetricOmmExtensions.beforeAddLiquidity, (sender, owner, salt, deltas, extensionData))
    );
  }
```

**File:** metric-core/contracts/ExtensionCalling.sol (L149-170)
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
```

**File:** metric-periphery/contracts/MetricOmmPoolLiquidityAdder.sol (L106-116)
```text
    try IMetricOmmPoolActions(pool)
      .addLiquidity(owner, salt, weightDeltas, abi.encode(KIND_PROBE), extensionData) returns (
      uint256, uint256
    ) {
      revert WeightedProbeInconclusive();
    } catch (bytes memory reason) {
      (uint256 need0, uint256 need1) = _decodeLiquidityProbeOrBubble(reason);
      LiquidityDelta memory scaled = _scaleWeightsToShares(weightDeltas, maxAmountToken0, maxAmountToken1, need0, need1);
      return _addLiquidity(pool, owner, salt, scaled, msg.sender, maxAmountToken0, maxAmountToken1, extensionData);
    }
  }
```

**File:** metric-periphery/contracts/MetricOmmPoolLiquidityAdder.sol (L183-207)
```text
  function _addLiquidity(
    address pool,
    address positionOwner,
    uint80 salt,
    LiquidityDelta memory deltas,
    address payer,
    uint256 maxAmountToken0,
    uint256 maxAmountToken1,
    bytes calldata extensionData
  ) internal returns (uint256 amount0Added, uint256 amount1Added) {
    _setPayContext(pool, payer, maxAmountToken0, maxAmountToken1);
    try IMetricOmmPoolActions(pool)
      .addLiquidity(positionOwner, salt, deltas, abi.encode(KIND_PAY), extensionData) returns (
      uint256 a0, uint256 a1
    ) {
      amount0Added = a0;
      amount1Added = a1;
      _clearPayContext();
    } catch (bytes memory reason) {
      _clearPayContext();
      assembly ("memory-safe") {
        revert(add(reason, 32), mload(reason))
      }
    }
  }
```

**File:** metric-core/contracts/interfaces/IMetricOmmPool/IMetricOmmPoolActions.sol (L146-162)
```text
  /// @notice Mint shares across bins for `(owner, salt)`; pulls tokens via `IMetricOmmModifyLiquidityCallback` on `msg.sender`.
  /// @dev Callback receives native token amounts the pool expects; underpay reverts `InsufficientTokenBalance`. If `DEPOSIT_ALLOWLIST_PROVIDER` is set, `owner` must pass allowlist. `msg.sender` pays but need not equal `owner` (operator pattern).
  /// @param owner Position owner encoded in the pool’s position key.
  /// @param salt Namespace byte width for the key (`uint80`).
  /// @param deltas Parallel `binIdxs` / `shares` arrays (see `LiquidityDelta`).
  /// @param callbackData Opaque bytes forwarded unmodified to the modify-liquidity callback.
  /// @param extensionData Opaque bytes forwarded to liquidity extensions (beforeAddLiquidity / afterAddLiquidity).
  /// @return amount0Added Total token0 actually pulled from the callback into the pool (native).
  /// @return amount1Added Total token1 actually pulled from the callback into the pool (native).
  /// @dev Reverts `LiquidityDeltaLengthMismatch` when `binIdxs` and `shares` lengths differ.
  function addLiquidity(
    address owner,
    uint80 salt,
    LiquidityDelta calldata deltas,
    bytes calldata callbackData,
    bytes calldata extensionData
  ) external returns (uint256 amount0Added, uint256 amount1Added);
```
