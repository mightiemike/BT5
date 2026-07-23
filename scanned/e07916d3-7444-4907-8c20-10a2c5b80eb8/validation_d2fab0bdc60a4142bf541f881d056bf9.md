### Title
`DepositAllowlistExtension` Checks LP-Owner Instead of Actual Depositor, Allowing Any Caller to Bypass the Deposit Guard — (`metric-periphery/contracts/extensions/DepositAllowlistExtension.sol`)

---

### Summary

`DepositAllowlistExtension.beforeAddLiquidity` silently drops the `sender` argument (the address that actually calls `addLiquidity` and provides tokens via callback) and instead gates on `owner` (the address that will receive LP shares). Because `MetricOmmPool.addLiquidity` lets any `msg.sender` specify an arbitrary `owner`, any unprivileged address can bypass the deposit allowlist by naming an already-allowlisted address as `owner`.

---

### Finding Description

`MetricOmmPool.addLiquidity` accepts a caller-supplied `owner` parameter and passes both `msg.sender` (the actual depositor) and `owner` (the LP-share recipient) into the extension hook: [1](#0-0) 

`ExtensionCalling._beforeAddLiquidity` forwards both values faithfully: [2](#0-1) 

`DepositAllowlistExtension.beforeAddLiquidity` receives `sender` as its first argument but leaves it **unnamed and unchecked**. The guard only inspects `owner`: [3](#0-2) 

`removeLiquidity` enforces `msg.sender == owner`, so LP shares cannot be reclaimed by the depositor: [4](#0-3) 

The token callback is issued to `msg.sender` (the actual caller), not to `owner`: [5](#0-4) 

---

### Impact Explanation

The deposit allowlist is the pool admin's mechanism to enforce a permissioned liquidity pool (e.g., institutional-only, KYC-gated, or whitelist-only). Because the guard checks the LP-share recipient rather than the token provider, the restriction is entirely ineffective:

- Any address not on the allowlist can call `pool.addLiquidity(allowlistedAddress, salt, deltas, callbackData, extensionData)`.
- The extension check passes (`allowedDepositor[pool][allowlistedAddress] == true`).
- The caller's tokens are pulled via the modify-liquidity callback.
- LP shares are credited to `allowlistedAddress`, not the caller.

Consequences:
1. **Admin-boundary break**: the pool admin's deposit restriction is bypassed by any unprivileged address.
2. **Bin-state manipulation**: an unauthorized party can inject tokens into specific bins, altering the pool's internal token0/token1 balance distribution across bins. In a pool without a swap allowlist, this can be combined with a swap to extract value at a manipulated bin position.
3. **Forced LP exposure**: an allowlisted address receives LP shares it never requested, giving it unintended exposure to pool risk.

---

### Likelihood Explanation

- No special role or privilege is required; any EOA or contract can call `addLiquidity`.
- The only prerequisite is knowing one allowlisted address for the target pool, which is observable on-chain via `AllowedToDepositSet` events or direct `allowedDepositor` reads.
- The attack is repeatable and costs only gas plus the deposited tokens (which the attacker does not recover).

---

### Recommendation

Replace the unnamed first parameter with `sender` and enforce the allowlist on the actual depositor:

```solidity
function beforeAddLiquidity(address sender, address owner, uint80, LiquidityDelta calldata, bytes calldata)
    external
    view
    override
    returns (bytes4)
{
    if (!allowAllDepositors[msg.sender]
        && !allowedDepositor[msg.sender][sender]   // check actual token provider
        && !allowedDepositor[msg.sender][owner])   // optionally also allow by owner
    {
        revert IMetricOmmPoolActions.NotAllowedToDeposit();
    }
    return IMetricOmmExtensions.beforeAddLiquidity.selector;
}
```

At minimum, `sender` (the address that provides tokens and triggers the callback) must be checked. Whether `owner` should also be checked is a policy decision for the pool admin.

---

### Proof of Concept

```
Setup:
  pool configured with DepositAllowlistExtension
  allowedDepositor[pool][alice] = true
  bob is NOT on the allowlist

Attack:
  bob calls pool.addLiquidity(
      owner        = alice,   // allowlisted address
      salt         = 0,
      deltas       = { binIdxs: [0], shares: [1e18] },
      callbackData = ...,
      extensionData = ""
  )

Extension check:
  allowedDepositor[pool][alice] == true  →  guard passes

Callback:
  pool calls bob.metricOmmModifyLiquidityCallback(...)
  bob transfers token0/token1 to the pool

Result:
  bob's tokens are now in the pool
  alice holds LP shares she never requested
  bob bypassed the deposit allowlist with zero privilege
```

### Citations

**File:** metric-core/contracts/MetricOmmPool.sol (L191-195)
```text
    _beforeAddLiquidity(msg.sender, owner, salt, deltas, extensionData);
    (amount0Added, amount1Added) = LiquidityLib.addLiquidity(
      _liquidityContext(), owner, salt, deltas, callbackData, binTotals, _binStates, _binTotalShares, _positionBinShares
    );
    _afterAddLiquidity(msg.sender, owner, salt, deltas, amount0Added, amount1Added, extensionData);
```

**File:** metric-core/contracts/MetricOmmPool.sol (L206-206)
```text
    if (msg.sender != owner) revert NotPositionOwner();
```

**File:** metric-core/contracts/ExtensionCalling.sol (L95-99)
```text
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

**File:** metric-core/contracts/libraries/LiquidityLib.sol (L14-20)
```text
/// @notice Holds `addLiquidity` / `removeLiquidity` logic as a deployable (DELEGATECALL-linked)
///         library so it does not inflate `MetricOmmPool` bytecode.
/// @dev Because every `public` function is called via DELEGATECALL from the pool:
///      - `msg.sender` is the original external caller.
///      - `address(this)` is the pool contract.
///      - Storage references resolve against the pool's state.
///      - ERC-20 calls (`balanceOf`, `safeTransfer`) operate on the pool's holdings.
```
