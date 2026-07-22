### Title
`DepositAllowlistExtension` validates `owner` instead of `sender`, allowing any caller to bypass the deposit guard — (`metric-periphery/contracts/extensions/DepositAllowlistExtension.sol`)

---

### Summary

The `DepositAllowlistExtension.beforeAddLiquidity` hook enforces its allowlist against the `owner` argument (the LP-position recipient), not against `sender` (the actual `msg.sender` of `addLiquidity`). Because `owner` is a freely chosen caller parameter, any non-allowlisted address can bypass the guard by passing an allowlisted address as `owner`.

---

### Finding Description

`MetricOmmPool.addLiquidity` accepts a caller-supplied `owner` address that designates who receives the LP position. The actual token provider is `msg.sender`, whose address is forwarded to the extension as `sender`:

```solidity
// MetricOmmPool.sol L191
_beforeAddLiquidity(msg.sender, owner, salt, deltas, extensionData);
```

The `DepositAllowlistExtension` override ignores `sender` (first parameter, unnamed) and checks only `owner`:

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

Attack path:

1. Pool is configured with `DepositAllowlistExtension`; Alice is allowlisted, Bob is not.
2. Bob calls `pool.addLiquidity(alice, salt, deltas, callbackData, extensionData)`.
3. The extension evaluates `allowedDepositor[pool][alice]` → `true` → no revert.
4. `LiquidityLib.addLiquidity` credits the position to Alice.
5. The pool calls `IMetricOmmSwapCallback(msg.sender).metricOmmAddLiquidityCallback(...)` on **Bob**, pulling Bob's tokens.

Bob has deposited into a restricted pool. The `SwapAllowlistExtension`, by contrast, correctly checks `sender` (the actual caller):

```solidity
// SwapAllowlistExtension.sol L37
if (!allowAllSwappers[msg.sender] && !allowedSwapper[msg.sender][sender]) {
```

The inconsistency between the two sibling extensions confirms the deposit check is mis-targeted.

---

### Impact Explanation

The `DepositAllowlistExtension` is the sole on-chain mechanism for restricting liquidity provision to permissioned pools (KYC, institutional, or otherwise gated). With the guard bypassed:

- Any address can add liquidity to a pool the admin intended to be restricted.
- The attacker can concentrate liquidity in specific bins to skew the pool's internal price position, then extract value via swaps if no swap allowlist is present.
- Compliance or regulatory invariants enforced by the allowlist are silently broken with no revert or event indicating a violation.

This constitutes a broken core pool functionality (access control on `addLiquidity`) with a direct path to fund-impacting pool-state manipulation.

---

### Likelihood Explanation

The trigger requires only a standard `addLiquidity` call with `owner` set to any allowlisted address. No privileged role, flash loan, or special token behavior is needed. Any non-allowlisted EOA or contract can execute this in a single transaction.

---

### Recommendation

Check `sender` (the actual depositor) instead of `owner` (the position recipient), mirroring the correct pattern in `SwapAllowlistExtension`:

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

If the intended semantics are truly "only allowlisted addresses may own positions," the extension name, NatSpec, and interface documentation must be updated to reflect that, and a separate `sender` check should be added to also gate the token-providing caller.

---

### Proof of Concept

```solidity
// Non-allowlisted Bob bypasses DepositAllowlistExtension

// Setup: pool has DepositAllowlistExtension; Alice is allowlisted, Bob is not.
// allowedDepositor[pool][alice] = true
// allowedDepositor[pool][bob]   = false (default)

// Bob calls addLiquidity with owner = alice
vm.prank(bob);
pool.addLiquidity(
    alice,          // owner — allowlisted, passes the guard
    0,              // salt
    deltas,
    callbackData,   // Bob's callback pays the tokens
    extensionData
);
// Extension checks allowedDepositor[pool][alice] == true → no revert
// Bob's tokens are pulled; Alice receives the LP position
// Bob has deposited into a pool he is not permitted to access
``` [1](#0-0) [2](#0-1) [3](#0-2)

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
