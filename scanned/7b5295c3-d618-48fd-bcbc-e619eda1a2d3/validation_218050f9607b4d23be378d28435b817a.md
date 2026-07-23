### Title
`DepositAllowlistExtension` Checks `owner` Instead of `sender`, Allowing Unauthorized Depositors to Bypass the Deposit Guard — (`metric-periphery/contracts/extensions/DepositAllowlistExtension.sol`)

---

### Summary

`DepositAllowlistExtension.beforeAddLiquidity` silently drops the `sender` parameter and checks `owner` (the LP-position holder) against the per-pool allowlist. Because `owner` is a free caller-supplied argument to `pool.addLiquidity`, any address that is not on the allowlist can deposit tokens into a restricted pool simply by naming an allowlisted address as `owner`. The guard is structurally misbound: it enforces the wrong actor, so the depositor restriction the pool admin believes is in place does not exist.

---

### Finding Description

`MetricOmmPool.addLiquidity` accepts two distinct address arguments:

```solidity
// MetricOmmPool.sol L182-L196
function addLiquidity(
    address owner,          // ← LP-position holder; caller-supplied, arbitrary
    uint80 salt,
    LiquidityDelta calldata deltas,
    bytes calldata callbackData,
    bytes calldata extensionData
) external nonReentrant(PoolActions.ADD_LIQUIDITY) ... {
    _beforeAddLiquidity(msg.sender, owner, salt, deltas, extensionData);
    ...
}
```

`msg.sender` (forwarded as `sender` to the hook) is the address that will be called back to pay tokens. `owner` is the address that will receive the LP shares. These are independent.

`ExtensionCalling._beforeAddLiquidity` passes both to the extension:

```solidity
// ExtensionCalling.sol L95-L98
abi.encodeCall(IMetricOmmExtensions.beforeAddLiquidity, (sender, owner, salt, deltas, extensionData))
```

The extension receives `sender` as its first argument and `owner` as its second. The guard implementation discards `sender` and checks only `owner`:

```solidity
// DepositAllowlistExtension.sol L32-L42
function beforeAddLiquidity(address, address owner, uint80, LiquidityDelta calldata, bytes calldata)
    external view override returns (bytes4)
{
    if (!allowAllDepositors[msg.sender] && !allowedDepositor[msg.sender][owner]) {
        revert IMetricOmmPoolActions.NotAllowedToDeposit();
    }
    return IMetricOmmExtensions.beforeAddLiquidity.selector;
}
```

`owner` is a value the caller chose freely. The actual depositor (`sender`) is never inspected.

Compare with `SwapAllowlistExtension`, which correctly checks `sender` (the swapper):

```solidity
// SwapAllowlistExtension.sol L31-L40
function beforeSwap(address sender, address, bool, int128, uint128, uint256, uint128, uint128, bytes calldata)
    external view override returns (bytes4)
{
    if (!allowAllSwappers[msg.sender] && !allowedSwapper[msg.sender][sender]) {
        revert IMetricOmmPoolActions.NotAllowedToSwap();
    }
```

The asymmetry is the defect: the swap guard checks the actor performing the action; the deposit guard checks the beneficiary of the action.

---

### Impact Explanation

**Admin-boundary break / broken allowlist guard.** The pool admin configures `DepositAllowlistExtension` believing it restricts which addresses can deposit tokens into the pool ("Gates `addLiquidity` by depositor address, per pool"). That restriction does not exist. Any address can deposit by supplying an allowlisted address as `owner`. The pool receives tokens from unauthorized sources, violating the access-control invariant the admin set up. For pools intended to be restricted to KYC'd or institutional LPs (the primary use-case for such an extension), the compliance boundary is entirely absent.

Secondary effect: an allowlisted depositor who legitimately wants to create a position on behalf of a non-allowlisted `owner` (e.g., a router creating positions for users) is incorrectly blocked, making the liquidity flow unusable in that configuration.

---

### Likelihood Explanation

Exploitation requires no special privilege. Any externally-owned account or contract can call `pool.addLiquidity` directly. The only prerequisite is knowing one allowlisted address for the target pool, which is readable from `allowedDepositor` (a public mapping). The bypass is a single-transaction, zero-setup operation.

---

### Recommendation

Mirror the pattern used by `SwapAllowlistExtension`: check `sender` (the depositor) instead of `owner` (the position holder).

```solidity
// DepositAllowlistExtension.sol — corrected beforeAddLiquidity
function beforeAddLiquidity(address sender, address, uint80, LiquidityDelta calldata, bytes calldata)
    external view override returns (bytes4)
{
    if (!allowAllDepositors[msg.sender] && !allowedDepositor[msg.sender][sender]) {
        revert IMetricOmmPoolActions.NotAllowedToDeposit();
    }
    return IMetricOmmExtensions.beforeAddLiquidity.selector;
}
```

If the intent is to gate by *both* depositor and owner, both should be checked explicitly.

---

### Proof of Concept

Setup:
- Pool `P` has `DepositAllowlistExtension` configured.
- `allowedDepositor[P][alice] = true`; Bob is not allowlisted.

Attack:
1. Bob calls `P.addLiquidity(alice, salt, deltas, callbackData, extensionData)`.
2. Pool calls `_beforeAddLiquidity(bob, alice, ...)`.
3. Extension evaluates `allowedDepositor[P][alice]` → `true` → no revert.
4. `LiquidityLib.addLiquidity` executes; the pool calls back to Bob to pull tokens.
5. Bob pays tokens; Alice receives LP shares she did not request.
6. Bob has deposited into a pool that was supposed to be closed to him. The allowlist guard was never triggered. [1](#0-0) [2](#0-1) [3](#0-2) [4](#0-3)

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
