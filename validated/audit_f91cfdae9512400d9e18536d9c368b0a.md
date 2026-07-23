Audit Report

## Title
`DepositAllowlistExtension` Checks `owner` Instead of `sender`, Allowing Any Address to Bypass the Deposit Allowlist — (`metric-periphery/contracts/extensions/DepositAllowlistExtension.sol`)

## Summary
`DepositAllowlistExtension.beforeAddLiquidity` silently discards the `sender` parameter (the actual token-providing caller) and gates access only on `owner` (the LP position recipient). Because `owner` is a caller-controlled argument, any unprivileged address can bypass the allowlist by passing an allowlisted address as `owner` while they themselves supply the tokens via the callback.

## Finding Description
`MetricOmmPool.addLiquidity` invokes the extension hook as:

```solidity
_beforeAddLiquidity(msg.sender, owner, salt, deltas, extensionData);
``` [1](#0-0) 

`ExtensionCalling._beforeAddLiquidity` encodes and forwards both `sender` (`msg.sender`) and `owner` (caller-supplied) to the extension:

```solidity
abi.encodeCall(IMetricOmmExtensions.beforeAddLiquidity, (sender, owner, salt, deltas, extensionData))
``` [2](#0-1) 

The interface explicitly names the first parameter `sender` and the second `owner`: [3](#0-2) 

`DepositAllowlistExtension.beforeAddLiquidity` drops `sender` (unnamed `address`) and checks only `owner`:

```solidity
function beforeAddLiquidity(address, address owner, uint80, LiquidityDelta calldata, bytes calldata)
    external view override returns (bytes4)
{
    if (!allowAllDepositors[msg.sender] && !allowedDepositor[msg.sender][owner]) {
        revert IMetricOmmPoolActions.NotAllowedToDeposit();
    }
``` [4](#0-3) 

`owner` is a free argument chosen by the caller — it is never validated against `msg.sender` in `addLiquidity` (unlike `removeLiquidity`, which enforces `msg.sender == owner`). [5](#0-4) 

`SwapAllowlistExtension.beforeSwap` correctly checks `sender`, confirming the deposit extension deviates from the intended pattern: [6](#0-5) 

## Impact Explanation
A pool admin deploys `DepositAllowlistExtension` to restrict liquidity provision to a curated set of addresses. Because the guard checks `owner` rather than `sender`, any unprivileged address can call `pool.addLiquidity(allowlistedAddress, ...)`, pass the allowlist check (since `allowedDepositor[pool][allowlistedAddress] == true`), supply tokens via the `IMetricOmmAddLiquidityCallback` callback, and successfully add liquidity to a restricted pool. The admin-boundary invariant is fully broken: an unprivileged address performs an action the pool admin explicitly restricted, influencing pool bin state and earning spread fees through positions they should not hold.

## Likelihood Explanation
Exploitation requires no special privileges, no flash loan, and no oracle manipulation. Any EOA or contract can call `addLiquidity` with a known allowlisted address as `owner`. The allowlisted addresses are discoverable on-chain via the public `allowedDepositor` mapping. [7](#0-6) 

## Recommendation
Change `beforeAddLiquidity` to check `sender` (the actual caller) instead of `owner`, mirroring `SwapAllowlistExtension`:

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
  - Pool P deployed with DepositAllowlistExtension E as beforeAddLiquidity hook.
  - Pool admin: allowedDepositor[P][Alice] = true.
  - Bob is NOT allowlisted: allowedDepositor[P][Bob] = false.

Attack:
  1. Bob calls P.addLiquidity(owner=Alice, salt, deltas, callbackData, extensionData).
  2. Pool calls E.beforeAddLiquidity(sender=Bob, owner=Alice, ...).
  3. Extension checks allowedDepositor[P][Alice] == true → no revert.
  4. Pool invokes Bob's IMetricOmmAddLiquidityCallback; Bob transfers tokens into pool.
  5. Alice receives LP position shares.

Result:
  - Bob, explicitly excluded by the pool admin, successfully adds liquidity.
  - The deposit allowlist is fully bypassed.
  - The pool admin's access-control invariant is broken.
```

### Citations

**File:** metric-core/contracts/MetricOmmPool.sol (L191-191)
```text
    _beforeAddLiquidity(msg.sender, owner, salt, deltas, extensionData);
```

**File:** metric-core/contracts/MetricOmmPool.sol (L206-206)
```text
    if (msg.sender != owner) revert NotPositionOwner();
```

**File:** metric-core/contracts/ExtensionCalling.sol (L95-98)
```text
    _callExtensionsInOrder(
      BEFORE_ADD_LIQUIDITY_ORDER,
      abi.encodeCall(IMetricOmmExtensions.beforeAddLiquidity, (sender, owner, salt, deltas, extensionData))
    );
```

**File:** metric-core/contracts/interfaces/extensions/IMetricOmmExtensions.sol (L14-20)
```text
  function beforeAddLiquidity(
    address sender,
    address owner,
    uint80 salt,
    LiquidityDelta calldata deltas,
    bytes calldata extensionData
  ) external returns (bytes4);
```

**File:** metric-periphery/contracts/extensions/DepositAllowlistExtension.sol (L13-13)
```text
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
