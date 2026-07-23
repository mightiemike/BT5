Audit Report

## Title
`SwapAllowlistExtension.beforeSwap` checks `sender` (direct pool caller / router) instead of `recipient` (actual trader), enabling allowlist bypass via `MetricOmmSimpleRouter` - (`metric-periphery/contracts/extensions/SwapAllowlistExtension.sol`)

## Summary

`SwapAllowlistExtension.beforeSwap` gates on the first parameter `sender`, which is `msg.sender` of the pool's `swap` call — the router contract when users trade through `MetricOmmSimpleRouter`. `DepositAllowlistExtension.beforeAddLiquidity` gates on `owner`, the actual position beneficiary. This asymmetry means a pool admin who allowlists the router address (necessary to enable any router-based swaps) simultaneously opens the swap gate to every user, including those the admin intended to exclude.

## Finding Description

`MetricOmmPool.swap` calls `_beforeSwap(msg.sender, recipient, ...)`: [1](#0-0) 

`ExtensionCalling._beforeSwap` forwards `sender` (= pool's `msg.sender`) as the first argument to every extension: [2](#0-1) 

`SwapAllowlistExtension.beforeSwap` ignores `recipient` and checks only `sender`: [3](#0-2) 

When a user calls `MetricOmmSimpleRouter.exactInputSingle`, the router calls `pool.swap(params.recipient, ...)`, making the pool's `msg.sender` the router address, not the user: [4](#0-3) 

So `sender` passed to `beforeSwap` = router address. The extension evaluates `allowedSwapper[pool][router]`, not `allowedSwapper[pool][user]`.

By contrast, `DepositAllowlistExtension.beforeAddLiquidity` ignores `sender` and checks `owner` — the actual position beneficiary: [5](#0-4) 

The integration test confirms the swap extension is configured to allowlist the `TestCaller` intermediary (`callers[0]`), not the end user (`users[0]`), and that the blocked-swap test relies on the intermediary not being allowlisted: [6](#0-5) 

**Exploit flow:**
1. Pool admin deploys pool with `SwapAllowlistExtension` in `beforeSwap`.
2. Admin calls `setAllowedToSwap(pool, routerAddress, true)` to enable router-based swaps (required for any user to trade via the standard periphery path).
3. Admin does **not** call `setAllowedToSwap(pool, malloryAddress, true)`.
4. Mallory calls `MetricOmmSimpleRouter.exactInputSingle(...)` with herself as `recipient`.
5. Pool calls `extension.beforeSwap(router, mallory, ...)`.
6. Extension checks `allowedSwapper[pool][router]` → `true` → swap proceeds.
7. Mallory trades in a pool explicitly configured to exclude her.

The deposit allowlist does not share this bypass: even if Mallory routes through a liquidity adder, `beforeAddLiquidity` checks `owner` (Mallory's address), which is not allowlisted, and the deposit reverts.

## Impact Explanation

A pool deploying `SwapAllowlistExtension` to enforce per-user trading restrictions (KYC, institutional counterparty curation, compliance gating) cannot enforce those restrictions for any user who routes through `MetricOmmSimpleRouter`. Allowlisting the router — the only way to enable the standard periphery path — simultaneously grants swap access to every user. The admin has no mechanism to simultaneously allow router-based swaps and restrict individual users. Unauthorized users can extract fees, cause price impact, and violate compliance constraints in pools explicitly configured to exclude them.

## Likelihood Explanation

`MetricOmmSimpleRouter` is the primary supported swap entrypoint. Any pool that (1) deploys `SwapAllowlistExtension` and (2) allowlists the router to support normal trading is immediately and permanently vulnerable. This is the expected operational pattern for a curated pool that still wants to support the standard periphery. No special attacker capability is required beyond calling the public router.

## Recommendation

Change `SwapAllowlistExtension.beforeSwap` to check `recipient` instead of `sender`, mirroring the `owner`-based check in `DepositAllowlistExtension`:

```solidity
// Before (checks the router/caller — wrong actor):
function beforeSwap(address sender, address, bool, int128, uint128, uint256, uint128, uint128, bytes calldata)
    external view override returns (bytes4)
{
    if (!allowAllSwappers[msg.sender] && !allowedSwapper[msg.sender][sender]) {

// After (checks the actual trader — correct actor):
function beforeSwap(address, address recipient, bool, int128, uint128, uint256, uint128, uint128, bytes calldata)
    external view override returns (bytes4)
{
    if (!allowAllSwappers[msg.sender] && !allowedSwapper[msg.sender][recipient]) {
```

This aligns the swap allowlist with the deposit allowlist's actor model: both gate on the economic beneficiary of the action, regardless of which intermediary relays the call.

## Proof of Concept

Extend `FullMetricExtensionTest`:

```solidity
function test_routerBypassSwapAllowlist() public {
    // Setup: allowlist callers[0] for deposit, add liquidity
    depositExtension.setAllowedToDeposit(address(pool), _getCallerAddress(0), true);
    _addLiquidity(0, -5, 4, 100_000, EXTENSION_TEST_SALT);

    // Admin allowlists the router to enable router-based swaps
    swapExtension.setAllowedToSwap(address(pool), address(router), true);
    // Admin does NOT allowlist users[1] (Mallory)

    // Mallory calls through the router — sender = router (allowlisted), bypass succeeds
    vm.prank(users[1]);
    router.exactInputSingle(ExactInputSingleParams({
        pool: address(pool),
        recipient: users[1],
        zeroForOne: false,
        amountIn: 1000,
        amountOutMinimum: 0,
        priceLimitX64: type(uint128).max,
        deadline: block.timestamp,
        tokenIn: address(token1),
        extensionData: ""
    }));
    // Swap succeeds — Mallory bypassed the per-user allowlist
}
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

**File:** metric-periphery/test/extensions/FullMetricExtension.t.sol (L55-74)
```text
  function test_blocksSwapWhenSwapperNotAllowed() public {
    depositExtension.setAllowedToDeposit(address(pool), _getCallerAddress(0), true);
    _addLiquidity(0, -5, 4, 100_000, EXTENSION_TEST_SALT);

    vm.expectRevert(IMetricOmmPoolActions.NotAllowedToSwap.selector);
    _swap(0, users[0], false, int128(1000), type(uint128).max);
  }

  function test_blocksDepositWhenDepositorNotAllowed() public {
    vm.expectRevert(IMetricOmmPoolActions.NotAllowedToDeposit.selector);
    _addLiquidity(0, -5, 4, 10_000, EXTENSION_TEST_SALT);
  }

  function test_allowedSwapSucceeds() public {
    depositExtension.setAllowedToDeposit(address(pool), _getCallerAddress(0), true);
    swapExtension.setAllowedToSwap(address(pool), address(callers[0]), true);

    _addLiquidity(0, -5, 4, 100_000, EXTENSION_TEST_SALT);
    _swap(0, users[0], false, int128(1000), type(uint128).max);
  }
```
