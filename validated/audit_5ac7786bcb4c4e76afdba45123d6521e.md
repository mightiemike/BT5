Audit Report

## Title
`DepositAllowlistExtension.beforeAddLiquidity` Gates on `owner` Instead of `sender`, Allowing Non-Allowlisted Callers to Bypass the Deposit Guard — (File: `metric-periphery/contracts/extensions/DepositAllowlistExtension.sol`)

## Summary

`DepositAllowlistExtension.beforeAddLiquidity` silently discards the `sender` argument (the address that actually calls `addLiquidity` and funds the position via callback) and instead checks only `owner` (the LP-position beneficiary). Any non-allowlisted address can bypass the guard by naming any allowlisted address as `owner`, injecting unauthorized liquidity into a restricted pool and corrupting bin balances that directly influence oracle-anchored swap prices for all users.

## Finding Description

`MetricOmmPool.addLiquidity` passes `msg.sender` as `sender` and the caller-supplied `owner` as distinct arguments to the extension hook:

```solidity
// metric-core/contracts/MetricOmmPool.sol L191
_beforeAddLiquidity(msg.sender, owner, salt, deltas, extensionData);
```

`ExtensionCalling._beforeAddLiquidity` correctly encodes both into the ABI call forwarded to each extension:

```solidity
// metric-core/contracts/ExtensionCalling.sol L95-98
abi.encodeCall(IMetricOmmExtensions.beforeAddLiquidity, (sender, owner, salt, deltas, extensionData))
```

`DepositAllowlistExtension.beforeAddLiquidity` receives both but leaves the first parameter (`sender`) unnamed and checks only `owner`:

```solidity
// metric-periphery/contracts/extensions/DepositAllowlistExtension.sol L32-42
function beforeAddLiquidity(address, address owner, uint80, LiquidityDelta calldata, bytes calldata)
    external view override returns (bytes4)
{
    if (!allowAllDepositors[msg.sender] && !allowedDepositor[msg.sender][owner]) {
        revert IMetricOmmPoolActions.NotAllowedToDeposit();
    }
    ...
}
```

The sibling `SwapAllowlistExtension.beforeSwap` correctly names and checks `sender` (first parameter):

```solidity
// metric-periphery/contracts/extensions/SwapAllowlistExtension.sol L31-41
function beforeSwap(address sender, address, ...)
{
    if (!allowAllSwappers[msg.sender] && !allowedSwapper[msg.sender][sender]) { ... }
}
```

**Exploit path:**
1. Pool `P` uses `DepositAllowlistExtension`; Alice is allowlisted, Bob is not.
2. Bob calls `pool.addLiquidity(owner = Alice, ...)`.
3. The guard evaluates `allowedDepositor[P][Alice] == true` → no revert. Bob (`sender`) is never checked.
4. The pool's `addLiquidity` callback is issued to Bob (`msg.sender`), who supplies the tokens.
5. `binTotals.scaledToken0` / `scaledToken1` are updated with Bob's tokens, corrupting the pool's liquidity distribution.
6. `removeLiquidity` enforces `msg.sender == owner`, so direct token recovery requires Bob to control Alice's address; however, the allowlist bypass and bin-balance corruption occur unconditionally.

## Impact Explanation

The `DepositAllowlistExtension` is the sole on-chain mechanism a pool admin has to restrict who may inject liquidity (e.g., for KYC/AML or risk-management purposes). The bypass breaks the core invariant "only allowlisted addresses may add liquidity" for every pool deploying this extension. Concretely:

- **Unauthorized liquidity injection**: Bob's tokens enter `binTotals` and per-bin `token0BalanceScaled`/`token1BalanceScaled`, shifting the pool's liquidity distribution without the admin's consent.
- **Bad-price execution for legitimate swappers**: Bin balances directly determine the oracle-anchored marginal price and the amount of tokens available at each price level. A non-allowlisted actor can concentrate or drain specific bins to force bad-price execution on subsequent swaps by legitimate users — a direct, in-scope impact.
- **Admin-boundary break**: An unprivileged address circumvents the pool admin's deposit restriction, which is an explicit allowed impact category.

## Likelihood Explanation

- No special privilege is required; any EOA or contract can call `addLiquidity`.
- The only prerequisite is knowing one allowlisted address, which is publicly readable from `allowedDepositor` storage or from emitted `AllowedToDepositSet` events.
- The exploit is a single transaction with no upfront cost beyond gas and the tokens deposited.
- Every pool that deploys `DepositAllowlistExtension` is immediately affected upon deployment.

## Recommendation

Replace the unnamed first parameter with `sender` and gate on it, matching the pattern used by `SwapAllowlistExtension`:

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

If the intended semantic is also "the LP-position owner must be allowlisted" (to prevent allowlisted LPs from gifting positions to non-allowlisted parties), both `sender` and `owner` should be checked.

## Proof of Concept

**Setup**
- Pool `P` deployed with `DepositAllowlistExtension` at `E`.
- Pool admin calls `E.setAllowedToDeposit(P, Alice, true)`. Bob is not allowlisted.

**Attack**
```solidity
// Bob (not allowlisted) calls addLiquidity naming Alice as owner
pool.addLiquidity(
    owner        = Alice,   // allowlisted → guard passes
    salt         = 0,
    deltas       = <desired bin shares>,
    callbackData = "",
    extensionData= ""
);
// Guard check: allowedDepositor[P][Alice] == true → no revert
// Pool issues callback to Bob (msg.sender) for tokens — Bob pays
// binTotals updated with Bob's tokens; Alice's LP position minted
```

**Foundry test sketch**
```solidity
function test_depositAllowlistBypass() public {
    // Alice allowlisted, Bob not
    extension.setAllowedToDeposit(address(pool), alice, true);

    // Bob calls addLiquidity with owner = alice
    vm.prank(bob);
    pool.addLiquidity(alice, 0, deltas, callbackData, "");
    // Should revert but does not — Bob bypassed the allowlist
}
``` [1](#0-0) [2](#0-1) [3](#0-2) [4](#0-3)

### Citations

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

**File:** metric-core/contracts/MetricOmmPool.sol (L188-196)
```text
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
