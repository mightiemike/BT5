Audit Report

## Title
`DepositAllowlistExtension.beforeAddLiquidity` Gates on `owner` Instead of `sender`, Allowing Any Caller to Bypass the Deposit Allowlist — (`metric-periphery/contracts/extensions/DepositAllowlistExtension.sol`)

## Summary
`DepositAllowlistExtension.beforeAddLiquidity` silently discards the `sender` argument (the actual token-paying caller) and validates only `owner` (the LP position recipient). Because `MetricOmmPool.addLiquidity` imposes no `msg.sender == owner` constraint, any unprivileged address can call `addLiquidity(owner = allowlistedAddress, …)`, pass the allowlist check, and inject liquidity into a restricted pool. This renders the pool admin's configured allowlist entirely ineffective.

## Finding Description

`DepositAllowlistExtension.beforeAddLiquidity` receives two address arguments — `sender` (the direct caller of `pool.addLiquidity`, i.e., the entity paying tokens via callback) and `owner` (the address that will own the resulting LP position). The implementation leaves `sender` unnamed and therefore completely ignored, checking only `owner`: [1](#0-0) 

`MetricOmmPool.addLiquidity` accepts a caller-supplied `owner` with no requirement that `msg.sender == owner`. This is in direct contrast to `removeLiquidity`, which explicitly enforces `if (msg.sender != owner) revert NotPositionOwner()`: [2](#0-1) [3](#0-2) 

The pool calls `_beforeAddLiquidity(msg.sender, owner, …)`, correctly forwarding both addresses to the extension: [4](#0-3) 

Because the extension ignores `sender` and only validates `owner`, an attacker who supplies an allowlisted address as `owner` will always pass the guard regardless of who `msg.sender` is. The correct pattern is already used in `SwapAllowlistExtension.beforeSwap`, which gates on `sender`: [5](#0-4) 

**Exploit path:**
1. Pool has `DepositAllowlistExtension` registered; `allowedDepositor[pool][alice] = true`; Bob is not allowlisted.
2. Bob calls `pool.addLiquidity(owner=alice, salt, deltas, callbackData, extensionData)`.
3. Pool calls `_beforeAddLiquidity(msg.sender=bob, owner=alice, …)`.
4. Extension receives `sender=bob` (unnamed, discarded) and `owner=alice` (checked). `allowedDepositor[pool][alice] == true` → no revert.
5. Bob's tokens enter the pool via callback; alice receives the LP position. The allowlist is bypassed.

## Impact Explanation

A pool deploying `DepositAllowlistExtension` intends to restrict which addresses may provide liquidity (e.g., regulatory compliance, curated LP sets, controlled bootstrapping). The bypass allows any unprivileged address to add liquidity to a restricted pool, breaking the admin-configured access-control invariant. This constitutes an **admin-boundary break**: an unprivileged path bypasses a factory/pool admin role check. Additionally, the attacker can shift liquidity distribution across bins in a restricted pool, affecting price impact and fee accrual for legitimate LPs, and can force LP position creation on allowlisted addresses without their consent. Every pool registering this shared singleton extension is simultaneously affected.

## Likelihood Explanation

No special privilege is required — any EOA or contract can call `pool.addLiquidity` directly. The attack is trivially constructable: the attacker only needs one allowlisted address, discoverable from on-chain `AllowedToDepositSet` events emitted by `setAllowedToDeposit`. There is no economic barrier to the bypass itself; the attacker pays tokens credited to the allowlisted owner. The bug is in the shared singleton extension contract, so all pools registering `DepositAllowlistExtension` are affected simultaneously.

## Recommendation

Replace the unnamed first parameter with `sender` and gate on the actual caller, not the position owner, mirroring the correct pattern in `SwapAllowlistExtension`:

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

```solidity
// Setup:
//   pool has DepositAllowlistExtension registered for beforeAddLiquidity
//   allowedDepositor[pool][alice] = true  (alice is allowlisted)
//   bob is NOT on the allowlist

// Attack: Bob calls pool.addLiquidity with owner = alice
pool.addLiquidity(
    alice,        // owner: allowlisted → extension check passes
    0,            // salt
    deltas,       // liquidity to add
    callbackData, // bob pays tokens via callback
    extensionData
);
// pool calls _beforeAddLiquidity(msg.sender=bob, owner=alice, …)
// extension receives sender=bob (unnamed, discarded), owner=alice (checked)
// allowedDepositor[pool][alice] == true → no revert
// Bob's tokens enter the pool; alice receives the LP position
// Deposit allowlist is completely bypassed
```

A Foundry integration test can confirm this by: (1) deploying a pool with `DepositAllowlistExtension`, (2) allowlisting `alice` via `setAllowedToDeposit`, (3) calling `pool.addLiquidity` from `bob` with `owner=alice`, and (4) asserting the call succeeds and LP shares are minted to `alice`.

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

**File:** metric-core/contracts/MetricOmmPool.sol (L206-206)
```text
    if (msg.sender != owner) revert NotPositionOwner();
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
