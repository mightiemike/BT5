The key code is already in hand. Let me confirm the exact check in `beforeAddLiquidity` and the `_validateOwner` logic before rendering a verdict.

The full picture is clear:

**`DepositAllowlistExtension.beforeAddLiquidity`** (line 32–42):
```solidity
function beforeAddLiquidity(address, address owner, uint80, LiquidityDelta calldata, bytes calldata)
    external view override returns (bytes4)
{
    if (!allowAllDepositors[msg.sender] && !allowedDepositor[msg.sender][owner]) {
        revert IMetricOmmPoolActions.NotAllowedToDeposit();
    }
    return IMetricOmmExtensions.beforeAddLiquidity.selector;
}
```

The first parameter (`sender` — the actual caller of `pool.addLiquidity`) is **explicitly unnamed and ignored**. The guard checks only `owner`.

**`MetricOmmPool.addLiquidity`** (line 191):
```solidity
_beforeAddLiquidity(msg.sender, owner, salt, deltas, extensionData);
```

`msg.sender` (the actual depositor/payer) is passed as `sender` but discarded by the extension.

**`_validateOwner`** (line 247–249):
```solidity
function _validateOwner(address owner) internal pure {
    if (owner == address(0)) revert InvalidPositionOwner();
}
```

Only rejects `address(0)`. Any non-zero allowlisted address is accepted as `owner`.

---

### Title
Deposit Allowlist Checks `owner` Instead of `sender`, Allowing Any Non-Allowlisted Actor to Deposit into Curated Pools — (`metric-periphery/contracts/extensions/DepositAllowlistExtension.sol`)

### Summary

`DepositAllowlistExtension.beforeAddLiquidity` ignores the `sender` argument (the actual caller/payer) and gates only on `owner` (the position recipient). Because `pool.addLiquidity` accepts any caller-supplied `owner`, a non-allowlisted attacker can set `owner` to any allowlisted address and pass the guard unconditionally.

### Finding Description

`MetricOmmPool.addLiquidity` passes `msg.sender` as `sender` and the caller-supplied `owner` as `owner` to `_beforeAddLiquidity`, which forwards both to the extension: [1](#0-0) 

The extension receives `(sender, owner, ...)` but discards `sender` entirely (unnamed first parameter) and checks only:

```solidity
!allowAllDepositors[msg.sender] && !allowedDepositor[msg.sender][owner]
``` [2](#0-1) 

`msg.sender` here is the pool (correct), but `owner` is the attacker-controlled position recipient. If the attacker sets `owner = allowlisted_address`, the check resolves to `allowedDepositor[pool][allowlisted_address]` = `true` and the guard passes — regardless of who is actually calling and paying.

The liquidity adder's `_validateOwner` only rejects `address(0)`: [3](#0-2) 

The pool itself imposes no restriction on who may call `addLiquidity` or what `owner` they supply. The attacker can also call the pool directly (bypassing the adder entirely) with any `owner`.

### Impact Explanation

The `DepositAllowlistExtension` is the sole mechanism for pool curation — restricting which addresses may add liquidity. With this bypass:

- A non-allowlisted attacker calls `pool.addLiquidity(allowlisted_address, salt, deltas, callbackData, extensionData)` directly (or via the adder).
- The extension passes because `allowedDepositor[pool][allowlisted_address]` is `true`.
- Tokens are pulled from the attacker; LP shares are minted to `allowlisted_address`.
- The pool admin cannot prevent non-allowlisted actors from adding liquidity to the curated pool.

The pool admin's curation invariant — that only allowlisted depositors can add liquidity — is completely broken. Any actor can inject liquidity into any bin, disrupting the pool's intended composition and price curve. The attacker loses their own tokens (no profit), but the allowlist provides zero protection.

### Likelihood Explanation

The attack requires only knowing one allowlisted address (publicly readable from `allowedDepositor`) and having tokens to spend. No privileged access, no special setup. The pool's `addLiquidity` is fully public.

### Recommendation

Check `sender` (the actual caller/payer) instead of `owner` in `beforeAddLiquidity`:

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

If the design intent is to allow paying on behalf of an allowlisted owner, then both `sender` and `owner` should be checked (either must be allowlisted, or both must be).

### Proof of Concept

```solidity
// attacker is NOT in allowedDepositor[pool]
// allowlisted_address IS in allowedDepositor[pool]

vm.prank(attacker); // attacker has tokens and implements callback
pool.addLiquidity(
    allowlisted_address, // owner = allowlisted → guard passes
    salt,
    deltas,
    callbackData,
    extensionData
);

// Assert: LP shares minted to allowlisted_address
// Assert: pool token balances increased
// Assert: attacker was never in allowedDepositor[pool]
``` [2](#0-1) [4](#0-3)

### Citations

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

**File:** metric-periphery/contracts/MetricOmmPoolLiquidityAdder.sol (L247-249)
```text
  function _validateOwner(address owner) internal pure {
    if (owner == address(0)) revert InvalidPositionOwner();
  }
```
