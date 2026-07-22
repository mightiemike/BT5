### Title
`DepositAllowlistExtension` Checks Position `owner` Instead of Transaction `sender`, Allowing Any Unauthorized User to Bypass the Deposit Allowlist — (`metric-periphery/contracts/extensions/DepositAllowlistExtension.sol`)

---

### Summary

`DepositAllowlistExtension.beforeAddLiquidity` silently discards the `sender` parameter (the actual depositor) and instead validates the caller-supplied `owner` (the position beneficiary). Because `owner` is a free parameter in `MetricOmmPool.addLiquidity`, any address not on the allowlist can bypass the guard by naming any allowlisted address as `owner`.

---

### Finding Description

`MetricOmmPool.addLiquidity` accepts an arbitrary `owner` address and passes both `msg.sender` (as `sender`) and `owner` to the extension hook:

```solidity
// MetricOmmPool.sol L191
_beforeAddLiquidity(msg.sender, owner, salt, deltas, extensionData);
```

The extension receives `(sender, owner, ...)` but the `DepositAllowlistExtension` implementation discards `sender` entirely (unnamed first parameter) and gates on `owner`:

```solidity
// DepositAllowlistExtension.sol L32-42
function beforeAddLiquidity(address, address owner, uint80, LiquidityDelta calldata, bytes calldata)
    external view override returns (bytes4)
{
    if (!allowAllDepositors[msg.sender] && !allowedDepositor[msg.sender][owner]) {
        revert IMetricOmmPoolActions.NotAllowedToDeposit();
    }
    return IMetricOmmExtensions.beforeAddLiquidity.selector;
}
```

`msg.sender` inside the extension is the pool (correct for pool-identity check), and `owner` is the position beneficiary chosen by the caller. Since `owner` is caller-controlled and unconstrained, any unauthorized user can pass the check by supplying any allowlisted address as `owner`.

By contrast, `SwapAllowlistExtension.beforeSwap` correctly reads the first parameter as `sender` and validates it:

```solidity
// SwapAllowlistExtension.sol L31-41
function beforeSwap(address sender, address, bool, int128, uint128, uint256, uint128, uint128, bytes calldata)
    external view override returns (bytes4)
{
    if (!allowAllSwappers[msg.sender] && !allowedSwapper[msg.sender][sender]) {
        revert IMetricOmmPoolActions.NotAllowedToSwap();
    }
```

The deposit extension has the wrong field bound to the allowlist lookup.

---

### Impact Explanation

The deposit allowlist is the pool admin's primary mechanism for restricting who may provide liquidity (e.g., KYC/compliance gating). With this bug the guard is entirely ineffective:

- Any unauthorized address can call `pool.addLiquidity(allowlisted_address, salt, deltas, ...)` directly.
- The extension sees `owner = allowlisted_address` → passes.
- Tokens are pulled from the unauthorized caller via the liquidity callback.
- The position is credited to `allowlisted_address`.
- The unauthorized caller cannot recover the tokens (only `owner` can call `removeLiquidity`), but a colluding allowlisted address can withdraw and share proceeds, making the pool effectively open to all.
- Even without collusion, the unauthorized caller can grief allowlisted addresses by forcing unwanted positions onto them, and can inject liquidity into a pool that was intended to be restricted.

This is a broken core pool invariant: the allowlist guard does not cover the actual depositing actor.

---

### Likelihood Explanation

Likelihood is **high**. The bypass requires no special privileges, no flash loan, and no complex setup. Any EOA can call `addLiquidity` directly on the pool with a known allowlisted address as `owner`. The allowlisted address set is often discoverable on-chain from past `AllowedToDepositSet` events.

---

### Recommendation

Bind the allowlist check to `sender` (the actual depositor), not `owner`:

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

This mirrors the correct pattern already used in `SwapAllowlistExtension.beforeSwap`.

---

### Proof of Concept

```solidity
// Assume pool is deployed with DepositAllowlistExtension.
// Only `allowedUser` is on the allowlist; `attacker` is not.

// Pool admin allowlists only allowedUser
depositExtension.setAllowedToDeposit(address(pool), allowedUser, true);

// Attacker (not allowlisted) calls addLiquidity directly,
// naming allowedUser as owner.
vm.startPrank(attacker);
token0.approve(address(pool), type(uint256).max);
token1.approve(address(pool), type(uint256).max);

// beforeAddLiquidity checks allowedDepositor[pool][owner] = allowedDepositor[pool][allowedUser] = true → passes
pool.addLiquidity(
    allowedUser,   // owner: allowlisted address → guard passes
    salt,
    deltas,
    callbackData,  // attacker pays tokens via callback
    extensionData
);
vm.stopPrank();

// Attacker bypassed the allowlist; liquidity is now in the pool
// Position is under allowedUser; attacker paid the tokens
// allowedUser can removeLiquidity and share proceeds with attacker
assertGt(pool.getPositionBinShares(allowedUser, salt, bin), 0);
```

**Root cause location:** [1](#0-0) 

**Pool hook dispatch (sender vs owner):** [2](#0-1) 

**Correct pattern in SwapAllowlistExtension:** [3](#0-2) 

**Extension interface showing both sender and owner are available:** [4](#0-3)

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
