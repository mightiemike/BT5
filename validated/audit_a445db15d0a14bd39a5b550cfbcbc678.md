Audit Report

## Title
`DepositAllowlistExtension.beforeAddLiquidity` Checks `owner` Instead of `sender`, Allowing Any Address to Bypass the Deposit Allowlist — (`metric-periphery/contracts/extensions/DepositAllowlistExtension.sol`)

## Summary
`DepositAllowlistExtension.beforeAddLiquidity` silently discards the `sender` parameter (the actual `msg.sender` of `addLiquidity`) and gates access on `owner`, which is a free caller-supplied argument. Because any caller can pass an allowlisted address as `owner`, the allowlist check is trivially bypassed by any address in a single transaction, rendering the extension's access control completely ineffective.

## Finding Description
`MetricOmmPool.addLiquidity` passes `msg.sender` as `sender` and the caller-supplied `owner` argument as `owner` to `_beforeAddLiquidity`: [1](#0-0) 

The interface `IMetricOmmExtensions.beforeAddLiquidity` defines the first parameter as `sender` and the second as `owner`: [2](#0-1) 

However, `DepositAllowlistExtension.beforeAddLiquidity` leaves the first parameter (`sender`) unnamed and unused, then checks only `owner`: [3](#0-2) 

The extension's NatSpec declares its purpose as gating by depositor address, and the mapping is named `allowedDepositor`, both confirming the intent is to check the actual depositing address (`sender`), not the LP-position beneficiary (`owner`): [4](#0-3) 

The correct pattern is already implemented in `SwapAllowlistExtension.beforeSwap`, which names and checks `sender` while leaving `recipient` unnamed: [5](#0-4) 

**Exploit path:**
1. Pool is deployed with `DepositAllowlistExtension`; only `Alice` is allowlisted.
2. `Charlie` (not allowlisted) calls `pool.addLiquidity(Alice, salt, deltas, callbackData, extensionData)`.
3. `beforeAddLiquidity` receives `sender = Charlie`, `owner = Alice`.
4. The guard evaluates `allowedDepositor[pool][Alice]` → `true` → no revert.
5. `Charlie`'s liquidity callback is invoked; `Charlie` pays the tokens; `Alice` receives LP shares.
6. `Charlie` has deposited into a pool that was supposed to block him.

No existing guard prevents this: the only check in `beforeAddLiquidity` is `allowedDepositor[msg.sender][owner]`, and `owner` is entirely attacker-controlled.

## Impact Explanation
The deposit allowlist is rendered completely ineffective. Any address — regardless of allowlist status — can inject liquidity into a pool configured with this extension by supplying any allowlisted address as `owner`. The pool admin's access-control intent is silently nullified, breaking the core liquidity-gating invariant the extension is deployed to enforce. This constitutes a broken core pool functionality causing an admin-boundary bypass by an unprivileged caller.

## Likelihood Explanation
Exploitation requires no special privilege, no flash loan, and no oracle manipulation. Any EOA or contract can trigger it in a single transaction by supplying any allowlisted address as `owner`. The bypass is unconditional and repeatable whenever the extension is configured on a pool.

## Recommendation
Replace the unnamed first parameter with `sender` and check it instead of `owner`, mirroring the correct pattern in `SwapAllowlistExtension`:

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
1. Deploy pool with `DepositAllowlistExtension` configured.
2. Pool admin calls `setAllowedToDeposit(pool, Alice, true)`; `Charlie` is not allowlisted.
3. `Charlie` calls `pool.addLiquidity(Alice, salt, deltas, callbackData, extensionData)`.
4. `beforeAddLiquidity(Charlie, Alice, ...)` is invoked; guard checks `allowedDepositor[pool][Alice]` → `true`.
5. No revert; `Charlie`'s callback executes; `Charlie` pays tokens; `Alice` receives LP shares.
6. Assert: `Charlie` successfully deposited despite not being on the allowlist.

### Citations

**File:** metric-core/contracts/MetricOmmPool.sol (L191-191)
```text
    _beforeAddLiquidity(msg.sender, owner, salt, deltas, extensionData);
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

**File:** metric-periphery/contracts/extensions/DepositAllowlistExtension.sol (L11-14)
```text
/// @notice Gates `addLiquidity` by depositor address, per pool.
contract DepositAllowlistExtension is BaseMetricExtension, IDepositAllowlistExtension {
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
