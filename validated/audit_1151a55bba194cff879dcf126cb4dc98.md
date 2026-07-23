### Title
SwapAllowlistExtension Checks Router Address Instead of Actual Swapper, Allowing Per-User Allowlist Bypass via MetricOmmSimpleRouter — (`metric-periphery/contracts/extensions/SwapAllowlistExtension.sol`)

---

### Summary

The `SwapAllowlistExtension` gates swaps by checking the `sender` parameter, which resolves to `msg.sender` of the pool — the router contract — rather than the actual end user. When swaps are routed through `MetricOmmSimpleRouter`, the extension sees the router's address as the swapper identity. This creates an irreconcilable actor mismatch: either the router is allowlisted (allowing all users to bypass per-user restrictions) or it is not (blocking even allowlisted users from using the router).

---

### Finding Description

`SwapAllowlistExtension.beforeSwap` checks `allowedSwapper[msg.sender][sender]`, where `msg.sender` is the pool (the extension's caller) and `sender` is the first parameter forwarded by the pool's extension dispatch: [1](#0-0) 

```solidity
function beforeSwap(address sender, address, bool, int128, uint128, uint256, uint128, uint128, bytes calldata)
    external view override returns (bytes4)
{
    if (!allowAllSwappers[msg.sender] && !allowedSwapper[msg.sender][sender]) {
        revert IMetricOmmPoolActions.NotAllowedToSwap();
    }
    return IMetricOmmExtensions.beforeSwap.selector;
}
```

The pool's `ExtensionCalling._beforeSwap` populates `sender` from the pool's own `msg.sender` — the calling contract (the router): [2](#0-1) 

When `MetricOmmSimpleRouter` calls `pool.swap(recipient, ...)`, the pool's `msg.sender` is the router, so `sender` = router address. The extension therefore checks whether the **router** is allowlisted, not the actual end user.

By contrast, `DepositAllowlistExtension.beforeAddLiquidity` checks `owner` — an explicit parameter that the router populates with the actual user's address: [3](#0-2) 

```solidity
function beforeAddLiquidity(address, address owner, uint80, LiquidityDelta calldata, bytes calldata)
    external view override returns (bytes4)
{
    if (!allowAllDepositors[msg.sender] && !allowedDepositor[msg.sender][owner]) {
        revert IMetricOmmPoolActions.NotAllowedToDeposit();
    }
    return IMetricOmmExtensions.beforeAddLiquidity.selector;
}
```

This inconsistency is confirmed by the integration test `test_allowedSwapSucceeds` in `metric-periphery/test/extensions/FullMetricExtension.t.sol`, which allowlists `address(callers[0])` (the intermediary contract) for swaps — not `users[0]` (the actual human user): [4](#0-3) 

```solidity
function test_allowedSwapSucceeds() public {
    depositExtension.setAllowedToDeposit(address(pool), _getCallerAddress(0), true);
    swapExtension.setAllowedToSwap(address(pool), address(callers[0]), true);  // router/caller, not users[0]
    _addLiquidity(0, -5, 4, 100_000, EXTENSION_TEST_SALT);
    _swap(0, users[0], false, int128(1000), type(uint128).max);
}
```

The test must allowlist the intermediary (`callers[0]`), not the end user (`users[0]`), proving the extension sees the intermediary's address as `sender`.

The `addLiquidity` interface explicitly separates payer from owner (operator pattern), so `owner` always carries the actual user: [5](#0-4) 

No equivalent explicit "swapper" parameter exists for `swap`, so the pool can only forward `msg.sender` (the router) as `sender`.

---

### Impact Explanation

A pool configured with `SwapAllowlistExtension` to restrict swaps to specific addresses (e.g., KYC'd users, curated LPs) is broken when `MetricOmmSimpleRouter` is used:

- **If the router is allowlisted** (required for users to swap through it): any user — including non-allowlisted users — can bypass the per-user swap restriction by routing through `MetricOmmSimpleRouter`. The allowlist provides zero per-user protection.
- **If the router is not allowlisted**: even allowlisted users cannot swap through the router, breaking core swap functionality for the pool.

Neither configuration achieves the intended per-user gating. This is a direct loss of the allowlist protection that pool admins configure to curate access, matching the "allowlist bypass" and "wrong-actor binding" impact classes.

---

### Likelihood Explanation

`MetricOmmSimpleRouter` is the primary supported swap entrypoint in the periphery layer. Any pool that configures `SwapAllowlistExtension` and expects users to interact through the router will be affected. The trigger requires no special privileges — any user can call the router directly. The mismatch is structural and present on every swap through the router.

---

### Recommendation

Two remediation paths:

1. **Add an explicit `swapper` parameter to `swap()`** (analogous to `owner` in `addLiquidity`), populated by the router with the actual user's address. The pool forwards this to `_beforeSwap` as `sender`. This mirrors the deposit pattern and restores per-user gating.

2. **Check `recipient` in `SwapAllowlistExtension`** if the intent is to gate by economic beneficiary (the address receiving output tokens). This is a weaker fix since `recipient` and the initiating user may differ.

Additionally, the NatSpec on `SwapAllowlistExtension` ("Gates `swap` by swapper address") should be corrected to reflect actual behavior until the root cause is fixed.

---

### Proof of Concept

1. Deploy a pool with `SwapAllowlistExtension` configured on `beforeSwap`.
2. Admin allowlists `userA` (a KYC'd user): `swapExtension.setAllowedToSwap(pool, userA, true)`.
3. `userB` (not allowlisted) calls `MetricOmmSimpleRouter.exactInput(pool, ...)`.
4. Router calls `pool.swap(recipient=userB, ...)` — pool's `msg.sender` = router.
5. Pool calls `_beforeSwap(sender=router, recipient=userB, ...)`.
6. Extension checks `allowedSwapper[pool][router]`.
7. If router is allowlisted (as required for normal operation): swap succeeds for `userB` despite not being allowlisted — **allowlist bypassed**.
8. If router is not allowlisted: swap also fails for `userA` — **core swap functionality broken**. [6](#0-5) [7](#0-6)

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

**File:** metric-periphery/test/extensions/FullMetricExtension.t.sol (L68-74)
```text
  function test_allowedSwapSucceeds() public {
    depositExtension.setAllowedToDeposit(address(pool), _getCallerAddress(0), true);
    swapExtension.setAllowedToSwap(address(pool), address(callers[0]), true);

    _addLiquidity(0, -5, 4, 100_000, EXTENSION_TEST_SALT);
    _swap(0, users[0], false, int128(1000), type(uint128).max);
  }
```

**File:** metric-core/contracts/interfaces/IMetricOmmPool/IMetricOmmPoolActions.sol (L146-150)
```text
  /// @notice Mint shares across bins for `(owner, salt)`; pulls tokens via `IMetricOmmModifyLiquidityCallback` on `msg.sender`.
  /// @dev Callback receives native token amounts the pool expects; underpay reverts `InsufficientTokenBalance`. If `DEPOSIT_ALLOWLIST_PROVIDER` is set, `owner` must pass allowlist. `msg.sender` pays but need not equal `owner` (operator pattern).
  /// @param owner Position owner encoded in the pool’s position key.
  /// @param salt Namespace byte width for the key (`uint80`).
  /// @param deltas Parallel `binIdxs` / `shares` arrays (see `LiquidityDelta`).
```
