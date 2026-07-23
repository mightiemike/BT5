Audit Report

## Title
`DepositAllowlistExtension.beforeAddLiquidity` Checks `owner` Instead of `sender`, Allowing Any Unlisted Depositor to Bypass the Allowlist - (File: `metric-periphery/contracts/extensions/DepositAllowlistExtension.sol`)

## Summary

`DepositAllowlistExtension.beforeAddLiquidity` silently discards the `sender` argument (the actual caller/payer) and checks only `owner` (the position recipient). Because `MetricOmmPoolLiquidityAdder.addLiquidityExactShares` allows any `msg.sender` to specify an arbitrary `owner`, any unlisted address can bypass the deposit allowlist by naming a listed address as `owner`. The deposit allowlist — the sole access-control gate on the `addLiquidity` path — is fully bypassed.

## Finding Description

`DepositAllowlistExtension.beforeAddLiquidity` receives `sender` as its first `address` parameter but discards it (unnamed), checking only `owner`: [1](#0-0) 

The pool passes `msg.sender` as `sender` and the caller-supplied `owner` as `owner`: [2](#0-1) 

`ExtensionCalling._beforeAddLiquidity` confirms both values are forwarded to the extension: [3](#0-2) 

`MetricOmmPoolLiquidityAdder.addLiquidityExactShares` allows any `msg.sender` (Alice, the payer) to specify an arbitrary `owner` (Bob, the position holder), passing `msg.sender` as the payer to the callback: [4](#0-3) 

The only guard on `owner` rejects only `address(0)`: [5](#0-4) 

The exploit path:
1. Alice (unlisted) calls `LiquidityAdder.addLiquidityExactShares(pool, bob, salt, deltas, ...)`
2. Adder calls `pool.addLiquidity(bob, salt, deltas, abi.encode(KIND_PAY), extensionData)`
3. Pool calls `_beforeAddLiquidity(msg.sender=LiquidityAdder, owner=bob, ...)`
4. Extension evaluates `allowedDepositor[pool][bob] == true` → passes
5. Alice's tokens are pulled via callback and deposited into Bob's position

Alice (the actual payer) is never checked. The NatDoc confirms the intent is to gate by depositor address, not owner: [6](#0-5) 

The analogous `SwapAllowlistExtension.beforeSwap` correctly checks `sender`, demonstrating the inconsistency: [7](#0-6) 

## Impact Explanation

Any address not on the allowlist can deposit into a restricted pool by routing through `MetricOmmPoolLiquidityAdder` and naming any allowlisted address as `owner`. The unlisted depositor's tokens enter the pool and the listed address receives the LP position. Pools configured as permissioned (KYC-gated, institution-only, or protocol-controlled) accept unauthorized deposits, breaking the core invariant that only approved parties may provide liquidity. This constitutes a broken core pool functionality causing unauthorized fund flows into restricted pools.

## Likelihood Explanation

The bypass requires no special privileges. Any EOA can call `MetricOmmPoolLiquidityAdder.addLiquidityExactShares` with a listed owner. Listed owner addresses are readable from on-chain `AllowedToDepositSet` events. `MetricOmmPoolLiquidityAdder` is a public periphery contract. No admin cooperation, malicious token, or unusual setup is required. The existing test `test_exactShares_canAddOnBehalfOfAnotherOwner` already demonstrates the owner/payer split works end-to-end: [8](#0-7) 

## Recommendation

Change `beforeAddLiquidity` to check `sender` (the actual depositor/payer) instead of `owner`, consistent with `SwapAllowlistExtension`:

```solidity
function beforeAddLiquidity(address sender, address, uint80, LiquidityDelta calldata, bytes calldata)
    external view override returns (bytes4)
{
    if (!allowAllDepositors[msg.sender] && !allowedDepositor[msg.sender][sender]) {
        revert IMetricOmmPoolActions.NotAllowedToDeposit();
    }
    return IMetricOmmExtensions.beforeAddLiquidity.selector;
}
```

## Proof of Concept

```
Setup:
  - Pool deployed with DepositAllowlistExtension in BEFORE_ADD_LIQUIDITY_ORDER
  - Admin calls setAllowedToDeposit(pool, bob, true)   // Bob is listed
  - Alice is NOT listed

Attack:
  1. Alice calls:
       LiquidityAdder.addLiquidityExactShares(pool, bob, salt, deltas, maxAmt0, maxAmt1, "")
  2. Adder calls:
       pool.addLiquidity(bob, salt, deltas, abi.encode(KIND_PAY), "")
  3. Pool calls:
       _beforeAddLiquidity(sender=LiquidityAdder, owner=bob, ...)
  4. Extension evaluates:
       allowedDepositor[pool][bob] == true  →  no revert
  5. LiquidityLib.addLiquidity mints shares to bob's position
  6. Callback pulls Alice's tokens into the pool

Result:
  - Alice (unlisted) successfully deposited into a restricted pool
  - Bob's position is credited; Alice paid
  - The allowlist check on Alice was never performed
```

### Citations

**File:** metric-periphery/contracts/extensions/DepositAllowlistExtension.sol (L10-13)
```text
/// @title DepositAllowlistExtension
/// @notice Gates `addLiquidity` by depositor address, per pool.
contract DepositAllowlistExtension is BaseMetricExtension, IDepositAllowlistExtension {
  mapping(address pool => mapping(address depositor => bool)) public allowedDepositor;
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

**File:** metric-core/contracts/MetricOmmPool.sol (L191-191)
```text
    _beforeAddLiquidity(msg.sender, owner, salt, deltas, extensionData);
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

**File:** metric-periphery/contracts/MetricOmmPoolLiquidityAdder.sol (L56-68)
```text
  function addLiquidityExactShares(
    address pool,
    address owner,
    uint80 salt,
    LiquidityDelta calldata deltas,
    uint256 maxAmountToken0,
    uint256 maxAmountToken1,
    bytes calldata extensionData
  ) external payable override returns (uint256 amount0Added, uint256 amount1Added) {
    _validateOwner(owner);
    _validateDeltas(deltas);
    return _addLiquidity(pool, owner, salt, deltas, msg.sender, maxAmountToken0, maxAmountToken1, extensionData);
  }
```

**File:** metric-periphery/contracts/MetricOmmPoolLiquidityAdder.sol (L247-249)
```text
  function _validateOwner(address owner) internal pure {
    if (owner == address(0)) revert InvalidPositionOwner();
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

**File:** metric-periphery/test/MetricOmmPoolLiquidityAdder.t.sol (L211-220)
```text
  function test_exactShares_canAddOnBehalfOfAnotherOwner() public {
    LiquidityDelta memory d = _deltaAbovePrice(4, 10_000);
    address bob = makeAddr("bob");

    vm.prank(alice);
    helper.addLiquidityExactShares(address(pool), bob, 1, d, type(uint256).max, type(uint256).max, "");

    uint256 bobShares = stateView.positionBinShares(address(pool), bob, 1, int8(4));
    assertGt(bobShares, 0);
  }
```
