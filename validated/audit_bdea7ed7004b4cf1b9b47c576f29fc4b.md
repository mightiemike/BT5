Audit Report

## Title
`DepositAllowlistExtension.beforeAddLiquidity` Ignores `sender`, Allowing Any Unprivileged Operator to Bypass the Deposit Allowlist — (`File: metric-periphery/contracts/extensions/DepositAllowlistExtension.sol`)

## Summary

`DepositAllowlistExtension.beforeAddLiquidity` receives both the depositing operator (`sender`) and the LP-position beneficiary (`owner`) but silently discards `sender` and only checks `owner` against the allowlist. Because `MetricOmmPool.addLiquidity` explicitly supports an operator pattern where `msg.sender` (the payer) need not equal `owner`, any unprivileged address can call `pool.addLiquidity(allowlistedOwner, …)` and pass the guard by borrowing an allowlisted owner's identity. The pool admin's configured access control is fully neutralised.

## Finding Description

`MetricOmmPool.addLiquidity` passes `msg.sender` as the first argument to the extension hook:

```solidity
// MetricOmmPool.sol line 191
_beforeAddLiquidity(msg.sender, owner, salt, deltas, extensionData);
``` [1](#0-0) 

`ExtensionCalling._beforeAddLiquidity` encodes this as `(sender, owner, …)` and forwards it to the extension:

```solidity
// ExtensionCalling.sol lines 95-98
abi.encodeCall(IMetricOmmExtensions.beforeAddLiquidity, (sender, owner, salt, deltas, extensionData))
``` [2](#0-1) 

`IMetricOmmExtensions` defines `sender` as the first parameter: [3](#0-2) 

`DepositAllowlistExtension.beforeAddLiquidity` receives both but leaves `sender` unnamed and never reads it — only `owner` is checked:

```solidity
// DepositAllowlistExtension.sol lines 32-42
function beforeAddLiquidity(address, address owner, uint80, LiquidityDelta calldata, bytes calldata)
    external view override returns (bytes4)
{
    if (!allowAllDepositors[msg.sender] && !allowedDepositor[msg.sender][owner]) {
        revert IMetricOmmPoolActions.NotAllowedToDeposit();
    }
    return IMetricOmmExtensions.beforeAddLiquidity.selector;
}
``` [4](#0-3) 

The `IMetricOmmPoolActions` NatSpec explicitly documents the operator pattern:

> "`msg.sender` pays but need not equal `owner` (operator pattern)." [5](#0-4) 

Additionally, `DepositAllowlistExtension.beforeAddLiquidity` drops the `onlyPool` modifier present in `BaseMetricExtension.beforeAddLiquidity`: [6](#0-5) 

By contrast, `SwapAllowlistExtension.beforeSwap` correctly checks `sender` (the actual swap initiator), not `recipient`: [7](#0-6) 

**Exploit path:**
1. Pool admin calls `extension.setAllowedToDeposit(pool, alice, true)` → `allowedDepositor[pool][alice] = true`; `bob` is NOT on the allowlist.
2. `bob` calls `pool.addLiquidity(alice, salt, deltas, callbackData, "")`.
3. Pool calls `extension.beforeAddLiquidity(bob, alice, salt, deltas, "")`.
4. Extension checks `allowedDepositor[pool][alice] == true` → passes; `bob` is never inspected.
5. Pool calls `bob.metricOmmModifyLiquidityCallback(…)` → `bob` pays tokens.
6. LP shares are minted under `(alice, salt)`.

The existing test suite confirms the extension only checks `owner` (the `depositor` argument in tests is always the `owner`, never the `sender`): [8](#0-7) 

## Impact Explanation

The pool admin deploys `DepositAllowlistExtension` to enforce a closed LP set (KYC-gated depositors, curated market-makers, regulatory whitelist). The bypass completely neutralises this admin-configured access control:

- Any unprivileged address can inject liquidity into an allowlist-gated pool.
- LP shares are minted to an address (`owner`) that did not initiate or consent to the deposit, enabling griefing or manipulation of that address's position accounting.
- If the pool's LP composition is security-sensitive (e.g., preventing adversarial LPs from influencing bin prices or stop-loss watermarks), the bypass directly undermines those downstream guards.

This is an **admin-boundary break**: an unprivileged path circumvents a pool-admin-configured access control with fund-relevant consequences (LP share issuance, pool token inflows). [9](#0-8) 

## Likelihood Explanation

- The operator pattern (`sender ≠ owner`) is a first-class, documented feature of `MetricOmmPool.addLiquidity`.
- No on-chain check prevents a direct call to `pool.addLiquidity` with an arbitrary `owner`; the periphery router is optional.
- The attacker only needs to know one allowlisted address, observable on-chain via `AllowedToDepositSet` events or the public `allowedDepositor` mapping.
- No special privilege, flash loan, or oracle manipulation is required — any EOA or contract can execute this.

Likelihood: **High**.

## Recommendation

Check both `sender` and `owner` in `beforeAddLiquidity`, and restore the `onlyPool` modifier:

```solidity
function beforeAddLiquidity(address sender, address owner, uint80, LiquidityDelta calldata, bytes calldata)
    external
    view
    override
    onlyPool
    returns (bytes4)
{
    address pool_ = msg.sender;
    bool senderOk = allowAllDepositors[pool_] || allowedDepositor[pool_][sender];
    bool ownerOk  = allowAllDepositors[pool_] || allowedDepositor[pool_][owner];
    if (!senderOk || !ownerOk) {
        revert IMetricOmmPoolActions.NotAllowedToDeposit();
    }
    return IMetricOmmExtensions.beforeAddLiquidity.selector;
}
```

Alternatively, if the intent is only to gate the LP-position owner (not the payer), the NatSpec and admin-facing documentation must be updated to make that explicit, and the `setAllowedToDeposit` / `setAllowAllDepositors` API should be renamed accordingly so pool admins are not misled.

## Proof of Concept

```
Setup:
  - Factory deploys pool with DepositAllowlistExtension on beforeAddLiquidity.
  - Pool admin calls extension.setAllowedToDeposit(pool, alice, true).
    → allowedDepositor[pool][alice] = true
  - bob is NOT on the allowlist.

Attack:
  1. bob calls pool.addLiquidity(alice, salt, deltas, callbackData, "")
       ↳ pool calls extension.beforeAddLiquidity(bob, alice, salt, deltas, "")
       ↳ extension checks allowedDepositor[pool][alice] == true  ✓
       ↳ extension returns selector — guard passes
  2. pool calls bob.metricOmmModifyLiquidityCallback(amount0, amount1, callbackData)
       ↳ bob pays token0/token1
  3. pool mints LP shares under (alice, salt)

Result:
  - bob (unprivileged) has successfully deposited into an allowlist-gated pool.
  - alice holds LP shares she did not initiate.
  - The deposit allowlist is completely bypassed.

Foundry test plan:
  - Extend DepositAllowlistSubExtension.t.sol with a test where vm.prank(address(pool))
    calls extension.beforeAddLiquidity(bob, alice, 0, emptyDelta, "") after only alice
    is allowlisted — assert it does NOT revert (demonstrating the bypass).
  - Extend FullMetricExtension.t.sol with an integration test where a non-allowlisted
    TestCaller calls pool.addLiquidity(allowlistedCallerAddress, ...) and succeeds.
```

### Citations

**File:** metric-core/contracts/MetricOmmPool.sol (L191-191)
```text
    _beforeAddLiquidity(msg.sender, owner, salt, deltas, extensionData);
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

**File:** metric-periphery/contracts/extensions/DepositAllowlistExtension.sol (L13-14)
```text
  mapping(address pool => mapping(address depositor => bool)) public allowedDepositor;
  mapping(address pool => bool) public allowAllDepositors;
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

**File:** metric-core/contracts/interfaces/IMetricOmmPool/IMetricOmmPoolActions.sol (L146-148)
```text
  /// @notice Mint shares across bins for `(owner, salt)`; pulls tokens via `IMetricOmmModifyLiquidityCallback` on `msg.sender`.
  /// @dev Callback receives native token amounts the pool expects; underpay reverts `InsufficientTokenBalance`. If `DEPOSIT_ALLOWLIST_PROVIDER` is set, `owner` must pass allowlist. `msg.sender` pays but need not equal `owner` (operator pattern).
  /// @param owner Position owner encoded in the pool’s position key.
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

**File:** metric-periphery/test/extensions/DepositAllowlistSubExtension.t.sol (L27-41)
```text
  function test_revertsWhenDepositorNotAllowed() public {
    vm.prank(address(pool));
    vm.expectRevert(IMetricOmmPoolActions.NotAllowedToDeposit.selector);
    LiquidityDelta memory emptyDelta = LiquidityDelta({binIdxs: new int256[](0), shares: new uint256[](0)});
    extension.beforeAddLiquidity(address(0), depositor, 0, emptyDelta, "");
  }

  function test_passesWhenDepositorAllowed() public {
    vm.prank(admin);
    extension.setAllowedToDeposit(address(pool), depositor, true);

    vm.prank(address(pool));
    LiquidityDelta memory emptyDelta = LiquidityDelta({binIdxs: new int256[](0), shares: new uint256[](0)});
    extension.beforeAddLiquidity(address(0), depositor, 0, emptyDelta, "");
  }
```
