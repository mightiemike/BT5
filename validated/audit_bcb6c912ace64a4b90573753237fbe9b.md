### Title
`DepositAllowlistExtension.beforeAddLiquidity` checks `owner` instead of `sender`, allowing any unprivileged caller to bypass the deposit allowlist guard — (`metric-periphery/contracts/extensions/DepositAllowlistExtension.sol`)

### Summary

`DepositAllowlistExtension` is designed to gate `addLiquidity` by depositor address. However, its `beforeAddLiquidity` hook checks the `owner` parameter (the LP position recipient) rather than the `sender` parameter (the actual caller paying tokens). Because `addLiquidity` accepts an arbitrary `owner` from any `msg.sender`, an unauthorized depositor can bypass the allowlist entirely by supplying an already-allowed address as `owner`.

### Finding Description

`MetricOmmPool.addLiquidity` accepts an arbitrary `owner` from any caller and passes both `msg.sender` (as `sender`) and `owner` to the extension hook: [1](#0-0) 

`ExtensionCalling._beforeAddLiquidity` encodes both `sender` and `owner` and forwards them to the extension: [2](#0-1) 

Inside `DepositAllowlistExtension.beforeAddLiquidity`, the first parameter (`sender`) is silently discarded (unnamed), and the guard is evaluated only against `owner`: [3](#0-2) 

The allowlist is keyed and named as `depositor`, strongly implying the intent is to restrict the calling depositor, not the LP recipient: [4](#0-3) 

By contrast, the analogous `SwapAllowlistExtension.beforeSwap` correctly checks `sender` (the swapper) and discards `recipient`: [5](#0-4) 

This asymmetry confirms the `owner`-check in `DepositAllowlistExtension` is a bug, not a design choice.

Additionally, `DepositAllowlistExtension.beforeAddLiquidity` drops the `onlyPool` modifier that `BaseMetricExtension` applies to every other hook, meaning the function can be called by any address directly: [6](#0-5) 

### Impact Explanation

Any unprivileged address can add liquidity to a pool that the admin intended to restrict to a whitelist of depositors. The attacker supplies an already-allowed address as `owner`, the allowlist check passes, the attacker pays tokens via the callback, and the allowed address receives the LP position. The pool admin's access-control boundary is fully bypassed: unauthorized parties can freely manipulate pool liquidity depth, alter bin state, and violate any compliance or risk-management intent behind the allowlist. The allowed address receives an unsolicited LP position it did not initiate.

### Likelihood Explanation

The `addLiquidity` function is public with no other caller restriction. Any address that knows a single allowed `owner` address (which is discoverable on-chain from `AllowedToDepositSet` events) can execute the bypass in a single transaction. No special privilege, flash loan, or oracle manipulation is required.

### Recommendation

Replace the `owner` check with a `sender` check in `beforeAddLiquidity`, mirroring `SwapAllowlistExtension`:

```solidity
function beforeAddLiquidity(address sender, address, uint80, LiquidityDelta calldata, bytes calldata)
    external
    view
    override
    onlyPool          // also restore the missing modifier
    returns (bytes4)
{
    if (!allowAllDepositors[msg.sender] && !allowedDepositor[msg.sender][sender]) {
        revert IMetricOmmPoolActions.NotAllowedToDeposit();
    }
    return IMetricOmmExtensions.beforeAddLiquidity.selector;
}
```

Also restore the `onlyPool` modifier (present in `BaseMetricExtension` but dropped by the override) to prevent direct external calls to the extension.

### Proof of Concept

```
Setup:
  pool configured with DepositAllowlistExtension
  allowedDepositor[pool][Alice] = true
  Bob is NOT in the allowlist

Attack:
  Bob calls pool.addLiquidity(
      owner    = Alice,   // allowed address used as bypass key
      salt     = 0,
      deltas   = <any valid delta>,
      callbackData = <Bob's callback pays his own tokens>,
      extensionData = ""
  )

Trace:
  pool._beforeAddLiquidity(sender=Bob, owner=Alice, ...)
  → extension.beforeAddLiquidity(Bob /*ignored*/, Alice, ...)
  → allowedDepositor[pool][Alice] == true  → guard passes
  → LiquidityLib.addLiquidity credits shares to (Alice, 0)
  → callback pulls tokens from Bob
  → Alice now holds LP position she did not create
  → Bob (unauthorized) has successfully added liquidity to a restricted pool
``` [3](#0-2)

### Citations

**File:** metric-core/contracts/MetricOmmPool.sol (L191-195)
```text
    _beforeAddLiquidity(msg.sender, owner, salt, deltas, extensionData);
    (amount0Added, amount1Added) = LiquidityLib.addLiquidity(
      _liquidityContext(), owner, salt, deltas, callbackData, binTotals, _binStates, _binTotalShares, _positionBinShares
    );
    _afterAddLiquidity(msg.sender, owner, salt, deltas, amount0Added, amount1Added, extensionData);
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

**File:** metric-periphery/contracts/extensions/DepositAllowlistExtension.sol (L13-19)
```text
  mapping(address pool => mapping(address depositor => bool)) public allowedDepositor;
  mapping(address pool => bool) public allowAllDepositors;

  constructor(address factory_) BaseMetricExtension(factory_) {}

  function setAllowedToDeposit(address pool_, address depositor, bool allowed) external onlyPoolAdmin(pool_) {
    allowedDepositor[pool_][depositor] = allowed;
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

**File:** metric-periphery/contracts/extensions/base/BaseMetricExtension.sol (L45-52)
```text
  function beforeAddLiquidity(address, address, uint80, LiquidityDelta calldata, bytes calldata)
    external
    virtual
    onlyPool
    returns (bytes4)
  {
    revert ExtensionNotImplemented();
  }
```
