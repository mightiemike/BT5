### Title
Deposit Allowlist Checks Wrong Actor (`owner` Instead of `sender`), Allowing Any Address to Bypass the Guard — (`File: metric-periphery/contracts/extensions/DepositAllowlistExtension.sol`)

### Summary
`DepositAllowlistExtension.beforeAddLiquidity` silently drops the `sender` parameter and gates on `owner` (the LP-position recipient) instead. Because `addLiquidity` lets the caller freely choose `owner`, any address can bypass the deposit restriction by naming an allowlisted address as the position owner while itself supplying the tokens.

### Finding Description
`MetricOmmPool.addLiquidity` passes two distinct actors to the extension hook: [1](#0-0) 

```
_beforeAddLiquidity(msg.sender /*sender*/, owner /*owner*/, salt, deltas, extensionData);
```

`sender` is the address that calls `addLiquidity` and must satisfy the swap-callback to deliver tokens. `owner` is the address that receives the LP-position shares. `removeLiquidity` enforces `msg.sender == owner`, so only `owner` can ever withdraw those shares. [2](#0-1) 

`DepositAllowlistExtension.beforeAddLiquidity` ignores `sender` entirely and checks only `owner`: [3](#0-2) 

```solidity
function beforeAddLiquidity(address, address owner, uint80, LiquidityDelta calldata, bytes calldata)
    external view override returns (bytes4)
{
    if (!allowAllDepositors[msg.sender] && !allowedDepositor[msg.sender][owner]) {
        revert IMetricOmmPoolActions.NotAllowedToDeposit();
    }
```

The first parameter (`sender`) is unnamed and unused. The guard therefore asks "is `owner` allowlisted?" rather than "is the caller allowlisted?".

Compare with `SwapAllowlistExtension.beforeSwap`, which correctly checks `sender` (the actual swapper): [4](#0-3) 

```solidity
function beforeSwap(address sender, address, ...)
    external view override returns (bytes4)
{
    if (!allowAllSwappers[msg.sender] && !allowedSwapper[msg.sender][sender]) {
        revert IMetricOmmPoolActions.NotAllowedToSwap();
    }
```

The naming of the public API (`isAllowedToDeposit`, `AllowedToDepositSet`, `setAllowedToDeposit`) and the parallel design with `SwapAllowlistExtension` confirm the intent is to gate the depositing actor, not the position owner. [5](#0-4) 

### Impact Explanation
A pool admin deploys a `DepositAllowlistExtension` to restrict liquidity provision to a curated set of addresses (e.g., KYC'd LPs, a single trusted liquidity manager). Because the guard checks `owner` rather than `sender`, any non-allowlisted address can call:

```
pool.addLiquidity(owner = <any allowlisted address>, salt, deltas, callbackData, extensionData)
```

The hook passes (the named `owner` is allowlisted), the caller delivers tokens through the swap callback, and the LP shares are minted to the allowlisted address. The deposit restriction is fully bypassed. Unauthorized liquidity enters the pool, violating the admin's access-control invariant. The allowlisted address receives an unsolicited position it did not initiate and may not want.

### Likelihood Explanation
The bypass requires only a single `addLiquidity` call with a publicly known allowlisted address as `owner`. No special privilege, flash loan, or multi-step setup is needed. Any address that can observe the allowlist (on-chain public state) can execute the bypass immediately.

### Recommendation
Replace the unnamed first parameter with `sender` and gate on it, mirroring `SwapAllowlistExtension`:

```diff
- function beforeAddLiquidity(address, address owner, uint80, LiquidityDelta calldata, bytes calldata)
+ function beforeAddLiquidity(address sender, address, uint80, LiquidityDelta calldata, bytes calldata)
      external view override returns (bytes4)
  {
-     if (!allowAllDepositors[msg.sender] && !allowedDepositor[msg.sender][owner]) {
+     if (!allowAllDepositors[msg.sender] && !allowedDepositor[msg.sender][sender]) {
          revert IMetricOmmPoolActions.NotAllowedToDeposit();
      }
```

Update `isAllowedToDeposit`, `setAllowedToDeposit`, and `setAllowAllDepositors` documentation to clarify that the gated entity is the caller of `addLiquidity`, not the LP-position owner.

### Proof of Concept
1. Pool is created with `DepositAllowlistExtension`; only `alice` is allowlisted (`allowedDepositor[pool][alice] = true`).
2. `bob` (not allowlisted) calls `pool.addLiquidity(owner=alice, salt=0, deltas=..., callbackData=..., extensionData=...)`.
3. `beforeAddLiquidity` is invoked with `sender=bob, owner=alice`. The check evaluates `allowedDepositor[pool][alice]` → `true` → no revert.
4. `bob` satisfies the token callback, depositing tokens he controls.
5. LP shares are minted to `alice`; `bob` has bypassed the deposit allowlist entirely.
6. `alice` can call `removeLiquidity` to recover the tokens; `bob` has lost his tokens but has successfully circumvented the pool admin's access control. [3](#0-2) [6](#0-5) [7](#0-6)

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

**File:** metric-core/contracts/MetricOmmPool.sol (L206-206)
```text
    if (msg.sender != owner) revert NotPositionOwner();
```

**File:** metric-periphery/contracts/extensions/DepositAllowlistExtension.sol (L18-29)
```text
  function setAllowedToDeposit(address pool_, address depositor, bool allowed) external onlyPoolAdmin(pool_) {
    allowedDepositor[pool_][depositor] = allowed;
    emit AllowedToDepositSet(pool_, depositor, allowed);
  }

  function setAllowAllDepositors(address pool_, bool allowed) external onlyPoolAdmin(pool_) {
    allowAllDepositors[pool_] = allowed;
    emit AllowAllDepositorsSet(pool_, allowed);
  }

  function isAllowedToDeposit(address pool_, address depositor) external view returns (bool) {
    return allowAllDepositors[pool_] || allowedDepositor[pool_][depositor];
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
