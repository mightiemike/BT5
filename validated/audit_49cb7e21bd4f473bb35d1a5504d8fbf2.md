Audit Report

## Title
`DepositAllowlistExtension.beforeAddLiquidity` Gates on `owner` Instead of `sender`, Allowing Any Caller to Bypass the Deposit Allowlist — (`metric-periphery/contracts/extensions/DepositAllowlistExtension.sol`)

## Summary
`DepositAllowlistExtension.beforeAddLiquidity` silently discards the `sender` argument (the actual `msg.sender` of `pool.addLiquidity`, i.e., the token payer) and checks only `owner` (the position recipient). Because `pool.addLiquidity` accepts an arbitrary `owner` address and has no caller restriction, any unprivileged address can bypass the allowlist by supplying an already-allowlisted address as `owner`. This voids any KYC/compliance deposit restriction the pool admin intended to enforce.

## Finding Description
`MetricOmmPool.addLiquidity` passes `msg.sender` as `sender` and the caller-supplied `owner` as the position recipient to the extension hook: [1](#0-0) 

`DepositAllowlistExtension.beforeAddLiquidity` receives `sender` as its first parameter but names it `address` (unnamed/discarded). The only allowlist check is on `owner`: [2](#0-1) 

The allowlist storage and admin setter are keyed by `depositor`, clearly implying the intent is to restrict the acting party, not the position recipient: [3](#0-2) 

By contrast, `SwapAllowlistExtension.beforeSwap` correctly gates on `sender` and discards `recipient`: [4](#0-3) 

`pool.addLiquidity` has no `onlyLiquidityAdder` or similar caller guard — it is fully public: [5](#0-4) 

The token transfer is demanded from `msg.sender` (the attacker) via `metricOmmModifyLiquidityCallback`, so the attacker pays and the allowlisted `owner` receives the position — the extension never checks who is actually paying.

## Impact Explanation
This is an **admin-boundary break**: a pool admin deploys `DepositAllowlistExtension` believing it restricts which addresses may deposit. In reality, any address can call `pool.addLiquidity(allowlistedOwner, ...)` directly. The extension sees `owner = allowlistedOwner` and passes. The unauthorized caller pays the tokens and the position is credited to the allowlisted address. KYC/compliance restrictions are silently voided with no on-chain signal to the admin. The allowlisted address also receives an unsolicited position it did not initiate, which may interfere with its own position management.

## Likelihood Explanation
Exploitation requires only a direct call to `pool.addLiquidity` with any already-allowlisted address as `owner`. No special privileges, flash loans, or oracle manipulation are needed. The allowlist state is publicly readable via `allowedDepositor` or `AllowedToDepositSet` events. The pool has no `onlyLiquidityAdder` guard. Any address can exploit this immediately and repeatedly.

## Recommendation
Change `beforeAddLiquidity` to gate on `sender` (the actual caller/payer), consistent with `SwapAllowlistExtension`:

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

If the intent is to restrict both who may call the pool **and** who may hold a position, gate on both `sender` and `owner`. Update `setAllowedToDeposit` / `isAllowedToDeposit` documentation to clarify which identity is being controlled.

## Proof of Concept
```
Setup:
  pool deployed with DepositAllowlistExtension (beforeAddLiquidity order set)
  admin calls setAllowedToDeposit(pool, alice, true)   // alice is allowlisted
  bob is NOT allowlisted

Attack:
  1. bob calls pool.addLiquidity(
         owner        = alice,   // allowlisted → extension passes
         salt         = 99,
         deltas       = { binIdxs: [0], shares: [10_000] },
         callbackData = abi.encode(...),  // bob implements metricOmmModifyLiquidityCallback
         extensionData = ""
     )
  2. Extension: allowedDepositor[pool][alice] == true → no revert
  3. Pool calls bob.metricOmmModifyLiquidityCallback(amount0, amount1, ...)
  4. bob transfers tokens to pool
  5. Position (alice, 99, bin 0) is credited with 10_000 shares

Result:
  - bob (not allowlisted) successfully deposited into the pool
  - alice holds an unsolicited position
  - allowlist boundary is silently bypassed
```

### Citations

**File:** metric-core/contracts/MetricOmmPool.sol (L182-196)
```text
  function addLiquidity(
    address owner,
    uint80 salt,
    LiquidityDelta calldata deltas,
    bytes calldata callbackData,
    bytes calldata extensionData
  ) external nonReentrant(PoolActions.ADD_LIQUIDITY) returns (uint256 amount0Added, uint256 amount1Added) {
    if (deltas.binIdxs.length == 0) return (0, 0);
    if (deltas.binIdxs.length != deltas.shares.length) revert LiquidityDeltaLengthMismatch();
    _beforeAddLiquidity(msg.sender, owner, salt, deltas, extensionData);
    (amount0Added, amount1Added) = LiquidityLib.addLiquidity(
      _liquidityContext(), owner, salt, deltas, callbackData, binTotals, _binStates, _binTotalShares, _positionBinShares
    );
    _afterAddLiquidity(msg.sender, owner, salt, deltas, amount0Added, amount1Added, extensionData);
  }
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
