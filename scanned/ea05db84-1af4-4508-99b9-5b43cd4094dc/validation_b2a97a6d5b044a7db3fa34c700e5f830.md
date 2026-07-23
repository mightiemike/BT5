### Title
`DepositAllowlistExtension` Checks `owner` Instead of `sender`, Allowing Any Caller to Bypass the Deposit Allowlist — (`File: metric-periphery/contracts/extensions/DepositAllowlistExtension.sol`)

### Summary

`DepositAllowlistExtension.beforeAddLiquidity` validates the `owner` argument (the LP-position beneficiary) rather than the `sender` argument (the actual `msg.sender` of `addLiquidity`, who provides the tokens via callback). Because `owner` is a free caller-supplied parameter, any address can bypass the deposit allowlist by naming an already-allowlisted address as `owner`.

### Finding Description

`MetricOmmPool.addLiquidity` passes two distinct addresses into the extension hook:

```
_beforeAddLiquidity(msg.sender /*sender*/, owner /*owner*/, salt, deltas, extensionData);
``` [1](#0-0) 

`ExtensionCalling._beforeAddLiquidity` forwards both to the extension:

```
abi.encodeCall(IMetricOmmExtensions.beforeAddLiquidity, (sender, owner, salt, deltas, extensionData))
``` [2](#0-1) 

Inside `DepositAllowlistExtension.beforeAddLiquidity`, the first parameter (`sender`) is silently discarded (unnamed `address`), and only `owner` is checked:

```solidity
function beforeAddLiquidity(address, address owner, uint80, LiquidityDelta calldata, bytes calldata)
    external view override returns (bytes4)
{
    if (!allowAllDepositors[msg.sender] && !allowedDepositor[msg.sender][owner]) {
        revert IMetricOmmPoolActions.NotAllowedToDeposit();
    }
    ...
}
``` [3](#0-2) 

The contract's own NatSpec states it "Gates `addLiquidity` by **depositor** address." The depositor is the entity that provides tokens — i.e., `sender` (`msg.sender` of `addLiquidity`), not `owner`. The token pull happens via the swap callback on `sender`: [4](#0-3) 

Because `owner` is a free parameter supplied by the caller, any unauthorized address can call:

```
pool.addLiquidity(allowlistedAddress, salt, deltas, callbackData, extensionData)
```

The extension sees `owner = allowlistedAddress` → check passes. The pool then pulls tokens from the unauthorized `msg.sender` via the callback. The LP position is minted to `allowlistedAddress`.

### Impact Explanation

The pool admin deployed `DepositAllowlistExtension` to enforce a closed set of depositors. The bypass lets any unprivileged address inject liquidity into the restricted pool, violating the admin-boundary invariant. Concrete consequences:

1. **Allowlist rendered ineffective**: Unauthorized parties participate in a pool explicitly configured to exclude them.
2. **Forced LP position on allowlisted address**: The allowlisted `owner` receives shares they did not request; they can withdraw them, but the pool's bin distribution and per-share accounting are altered without their consent.
3. **Pool-state manipulation**: An attacker willing to sacrifice tokens can shift bin balances, `curBinIdx`, and `curPosInBin`, affecting subsequent oracle-anchored swap prices for legitimate LPs.

This is an admin-boundary break: a factory/pool-admin access control is bypassed by an unprivileged path.

### Likelihood Explanation

- **Trigger**: Any external address can call `addLiquidity` on the pool directly (no special role required).
- **Cost**: The attacker must supply real tokens via the callback; they lose those tokens to the allowlisted `owner`'s position.
- **Knowledge required**: The attacker only needs to know one allowlisted address (publicly readable via `allowedDepositor`).
- **Likelihood**: Medium — the attacker bears a token cost, but the bypass is unconditional and requires no privileged access.

### Recommendation

Replace the `owner` check with a `sender` check in `beforeAddLiquidity`:

```solidity
// Before (wrong — checks position owner, not token provider)
function beforeAddLiquidity(address, address owner, uint80, LiquidityDelta calldata, bytes calldata)
    external view override returns (bytes4)
{
    if (!allowAllDepositors[msg.sender] && !allowedDepositor[msg.sender][owner]) { ... }
}

// After (correct — checks the actual caller who provides tokens)
function beforeAddLiquidity(address sender, address, uint80, LiquidityDelta calldata, bytes calldata)
    external view override returns (bytes4)
{
    if (!allowAllDepositors[msg.sender] && !allowedDepositor[msg.sender][sender]) { ... }
}
``` [3](#0-2) 

If the intent is to restrict both the caller and the position owner, both should be checked independently.

### Proof of Concept

1. Pool is deployed with `DepositAllowlistExtension` as a `beforeAddLiquidity` hook.
2. Pool admin calls `setAllowedToDeposit(pool, alice, true)`. Only `alice` is allowlisted.
3. `bob` (not allowlisted) constructs a valid `LiquidityDelta` and calls:
   ```
   pool.addLiquidity(
       alice,          // owner — allowlisted, passes the check
       salt,
       deltas,
       callbackData,   // bob's callback pays the tokens
       extensionData
   );
   ```
4. `DepositAllowlistExtension.beforeAddLiquidity` receives `owner = alice` → `allowedDepositor[pool][alice] == true` → no revert.
5. `LiquidityLib.addLiquidity` executes; the pool calls `bob`'s `metricOmmAddLiquidityCallback`, pulling tokens from `bob`.
6. LP shares are minted to `alice`. `bob` has deposited into the restricted pool without being allowlisted. [5](#0-4) [6](#0-5)

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

**File:** metric-core/contracts/MetricOmmPool.sol (L258-263)
```text
      IMetricOmmSwapCallback(msg.sender).metricOmmSwapCallback(amount0Delta, amount1Delta, callbackData);
      // casting to uint256 is safe because amount0Delta is positive and the ammount of tokens in pool is capped by uint128.max
      // forge-lint: disable-next-line(unsafe-typecast)
      if (amount0Delta > 0 && balance0Before + uint256(amount0Delta) > balance0()) {
        revert IncorrectDelta();
      }
```

**File:** metric-core/contracts/ExtensionCalling.sol (L95-98)
```text
    _callExtensionsInOrder(
      BEFORE_ADD_LIQUIDITY_ORDER,
      abi.encodeCall(IMetricOmmExtensions.beforeAddLiquidity, (sender, owner, salt, deltas, extensionData))
    );
```

**File:** metric-periphery/contracts/extensions/DepositAllowlistExtension.sol (L12-42)
```text
contract DepositAllowlistExtension is BaseMetricExtension, IDepositAllowlistExtension {
  mapping(address pool => mapping(address depositor => bool)) public allowedDepositor;
  mapping(address pool => bool) public allowAllDepositors;

  constructor(address factory_) BaseMetricExtension(factory_) {}

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
  }

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
