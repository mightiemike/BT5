### Title
`DepositAllowlistExtension` Checks `owner` Instead of `sender`, Allowing Any Caller to Bypass the Deposit Allowlist — (`metric-periphery/contracts/extensions/DepositAllowlistExtension.sol`)

---

### Summary

`DepositAllowlistExtension.beforeAddLiquidity` gates deposits by checking the `owner` parameter (the LP position recipient) rather than the `sender` parameter (the actual caller who provides tokens). Any unprivileged address can bypass the allowlist by calling `pool.addLiquidity(allowlisted_address, ...)`, causing the extension to approve the deposit because the allowlisted address appears as `owner`.

---

### Finding Description

In `MetricOmmPool.addLiquidity`, the pool calls the extension hook as:

```solidity
_beforeAddLiquidity(msg.sender, owner, salt, deltas, extensionData);
``` [1](#0-0) 

Here `sender = msg.sender` (the actual caller who will pay tokens via the swap callback) and `owner` is a caller-supplied address that receives the LP position shares.

`DepositAllowlistExtension.beforeAddLiquidity` then performs:

```solidity
function beforeAddLiquidity(address, address owner, uint80, LiquidityDelta calldata, bytes calldata)
    external view override returns (bytes4)
{
    if (!allowAllDepositors[msg.sender] && !allowedDepositor[msg.sender][owner]) {
        revert IMetricOmmPoolActions.NotAllowedToDeposit();
    }
    return IMetricOmmExtensions.beforeAddLiquidity.selector;
}
``` [2](#0-1) 

The first parameter (`sender`, the actual depositor) is silently discarded (named `address,`). The guard checks `owner` — a value the caller controls freely. Because `owner` is an arbitrary caller-supplied address, any unprivileged address can pass the allowlist check by supplying an allowlisted address as `owner`.

This is structurally identical to the Merkle tree bug: the guard tests the wrong variable (`owner` instead of `sender`), so the invariant it is supposed to enforce — "only allowlisted addresses may deposit" — is never actually checked against the entity performing the action.

Compare with `SwapAllowlistExtension.beforeSwap`, which correctly checks `sender` (the actual swapper):

```solidity
function beforeSwap(address sender, address, ...)
    external view override returns (bytes4)
{
    if (!allowAllSwappers[msg.sender] && !allowedSwapper[msg.sender][sender]) {
        revert IMetricOmmPoolActions.NotAllowedToSwap();
    }
``` [3](#0-2) 

The deposit extension deviates from this correct pattern.

---

### Impact Explanation

An unauthorized address can deposit into a pool whose admin intended to restrict liquidity provision to a curated set of addresses. Consequences:

1. **Allowlist bypass / admin-boundary break**: The pool admin's access control is silently nullified. Any address can add liquidity regardless of allowlist status.
2. **Forced LP position on allowlisted address**: The unauthorized depositor provides tokens via the callback; the LP shares are minted to the allowlisted `owner` address without that address's consent. While `removeLiquidity` enforces `msg.sender == owner`, the allowlisted address is now burdened with an unwanted position in a pool they may not have chosen.
3. **Pool dilution**: Unauthorized liquidity additions dilute existing LPs' share of pool fees and assets, directly reducing their owed LP claims.

---

### Likelihood Explanation

- The attack requires no special privilege — any EOA or contract can call `pool.addLiquidity`.
- The attacker only needs to know one allowlisted address (publicly readable from `allowedDepositor`).
- The attacker must supply tokens (they lose them to the pool), but the primary goal is allowlist bypass and griefing, not profit extraction for the attacker.
- Pools using `DepositAllowlistExtension` for access control are the entire target surface; the bug is present in every such pool.

---

### Recommendation

Change the `beforeAddLiquidity` check to use the `sender` parameter (the actual depositor/caller) instead of `owner`:

```solidity
function beforeAddLiquidity(address sender, address owner, uint80, LiquidityDelta calldata, bytes calldata)
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

1. Pool admin deploys a pool with `DepositAllowlistExtension` configured in `BEFORE_ADD_LIQUIDITY_ORDER`.
2. Pool admin calls `setAllowedToDeposit(pool, alice, true)` — only Alice is permitted to deposit.
3. Unauthorized address Bob calls:
   ```solidity
   pool.addLiquidity(
       alice,          // owner — allowlisted, passes the guard
       salt,
       deltas,
       callbackData,   // Bob's callback pays the tokens
       extensionData
   );
   ```
4. `beforeAddLiquidity` is invoked with `sender = Bob`, `owner = Alice`. The extension ignores `sender` and checks `allowedDepositor[pool][alice] == true` → passes.
5. Bob's callback transfers tokens into the pool; Alice receives LP shares she did not request.
6. Bob has bypassed the allowlist. The pool admin's access control is broken. [2](#0-1) [4](#0-3)

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
