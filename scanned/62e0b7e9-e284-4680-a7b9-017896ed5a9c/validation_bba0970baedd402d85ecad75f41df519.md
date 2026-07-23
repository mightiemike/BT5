### Title
`DepositAllowlistExtension.beforeAddLiquidity` checks `owner` instead of `sender`, allowing any unprivileged caller to bypass the deposit allowlist — (File: metric-periphery/contracts/extensions/DepositAllowlistExtension.sol)

### Summary
`DepositAllowlistExtension` is designed to gate `addLiquidity` by depositor address. Its `beforeAddLiquidity` hook silently drops the `sender` parameter and checks `owner` instead. Because `owner` is a caller-supplied argument (not the actual payer), any address can bypass the allowlist by specifying an allowlisted address as `owner` while remaining the true depositor (`sender`/`msg.sender` at the pool level).

### Finding Description
In `MetricOmmPool.addLiquidity`, the pool calls the extension with two distinct addresses: [1](#0-0) 

- `sender` = `msg.sender` of the pool call (the actual caller who pays tokens via the callback)
- `owner` = the `owner` argument passed to `addLiquidity` (who will own the resulting position)

`DepositAllowlistExtension.beforeAddLiquidity` silently discards `sender` (first parameter is unnamed) and gates only on `owner`: [2](#0-1) 

By contrast, `SwapAllowlistExtension.beforeSwap` correctly names and checks `sender`: [3](#0-2) 

**Exploit path:**
1. Pool admin deploys a pool with `DepositAllowlistExtension` and allowlists only `Alice`.
2. Attacker Bob controls a second address `Alice` (e.g., a contract he deployed and got allowlisted, or any allowlisted address he controls).
3. Bob calls `pool.addLiquidity(owner=Alice, salt=0, deltas=..., callbackData=..., extensionData=...)` from his non-allowlisted address.
4. The extension evaluates `allowedDepositor[pool][Alice]` → `true` and does not revert.
5. Bob pays tokens via the `metricOmmModifyLiquidityCallback` (called back to `msg.sender` = Bob).
6. The position is minted under `Alice`'s ownership.
7. Bob calls `pool.removeLiquidity` through `Alice` to recover tokens and fees.

Bob has effectively added liquidity to a restricted pool, earned spread and notional fees, and withdrawn — all while never appearing on the allowlist himself. [4](#0-3) 

### Impact Explanation
The deposit allowlist is the pool admin's primary mechanism for restricting LP composition (e.g., KYC/AML compliance, private pools, curated LP sets). Bypassing it allows an unprivileged address to:
- Add liquidity to a pool it is explicitly excluded from.
- Earn protocol spread fees and notional fees from that pool.
- Dilute or front-run authorized LPs.

This is a direct admin-boundary break: a pool-admin-configured guard is bypassed by an unprivileged caller path, with fee-earning (fund-impacting) consequences.

### Likelihood Explanation
The precondition is low: the attacker only needs to know one allowlisted address they control (or can use as a pass-through). No privileged keys, no flash loans, no oracle manipulation required. The call is a standard `addLiquidity` with a crafted `owner` argument. Any pool deploying `DepositAllowlistExtension` is affected.

### Recommendation
Replace the unnamed first parameter with `sender` and gate on it, matching the intent of the extension and the pattern used by `SwapAllowlistExtension`:

```solidity
function beforeAddLiquidity(address sender, address, uint80, LiquidityDelta calldata, bytes calldata)
    external
    view
    override
    returns (bytes4)
{
    if (!allowAllDepositors[msg.sender] && !allowedDepositor[msg.sender][sender]) {
        revert IMetricOmmPoolActions.NotAllowedToDeposit();
    }
    return IMetricOmmExtensions.beforeAddLiquidity.selector;
}
``` [2](#0-1) 

### Proof of Concept

```
Setup:
  pool = MetricOmmPool with DepositAllowlistExtension as beforeAddLiquidity hook
  pool admin calls setAllowedToDeposit(pool, Alice, true)
  Bob is NOT on the allowlist

Attack:
  Bob deploys/controls Alice (or obtains any allowlisted address he controls)
  Bob calls pool.addLiquidity(owner=Alice, salt=0, deltas=<valid>, callbackData=<Bob pays>, extensionData=<>)

Extension check:
  allowedDepositor[pool][Alice] == true  →  no revert

Result:
  Bob's tokens enter the pool (paid via callback to Bob)
  Position minted for Alice (controlled by Bob)
  Bob calls pool.removeLiquidity through Alice, recovering tokens + accrued fees
  Deposit allowlist completely bypassed
```

### Citations

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

**File:** metric-periphery/contracts/extensions/SwapAllowlistExtension.sol (L31-40)
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
