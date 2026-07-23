### Title
`DepositAllowlistExtension` Checks `owner` Instead of `sender`, Allowing Any Caller to Bypass the Deposit Allowlist — (`metric-periphery/contracts/extensions/DepositAllowlistExtension.sol`)

---

### Summary

`DepositAllowlistExtension.beforeAddLiquidity` gates deposits by checking the LP position `owner` parameter rather than the actual transaction initiator (`sender`). Any unprivileged address can bypass the allowlist by calling `addLiquidity` with `owner` set to an allowlisted address, injecting tokens into the pool and creating an unwanted position for the allowlisted party.

---

### Finding Description

`MetricOmmPool.addLiquidity` passes two distinct addresses to the extension hook:

- `sender` = `msg.sender` (the address that called `addLiquidity` and will pay tokens via the callback)
- `owner` = the LP position owner supplied as a calldata parameter [1](#0-0) 

`ExtensionCalling._beforeAddLiquidity` forwards both to the extension: [2](#0-1) 

`DepositAllowlistExtension.beforeAddLiquidity` then checks `owner`, not `sender`:

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

The parallel `SwapAllowlistExtension.beforeSwap` correctly checks `sender` (the actual swap initiator): [4](#0-3) 

The inconsistency is the root cause: the deposit guard checks who *owns* the resulting position, not who *provides the tokens and triggers the deposit*.

---

### Impact Explanation

An unauthorized caller (Bob, not on the allowlist) calls:

```
pool.addLiquidity(alice, salt, deltas, callbackData, extensionData)
```

where `alice` is an allowlisted address. The extension evaluates `allowedDepositor[pool][alice]` → `true` and does not revert. The pool's swap callback fires against `msg.sender` (Bob), pulling Bob's tokens into the pool. Alice receives the LP position shares.

Consequences:
- **Allowlist fully bypassed**: any unprivileged address can deposit into a pool that the admin intended to restrict.
- **Unauthorized token injection**: tokens from non-allowlisted parties enter the pool, violating the pool admin's access-control invariant.
- **Unwanted position forced on allowlisted user**: Alice receives LP shares she did not request; if she does not notice and remove them, she bears impermanent-loss exposure on tokens she never chose to commit.
- **Bob suffers permanent token loss**: the callback debits Bob's balance; the position belongs to Alice, so Bob cannot recover the tokens.

This is an admin-boundary break: the pool admin's allowlist configuration is bypassed by an unprivileged path, directly contradicting the stated purpose of the extension ("Gates `addLiquidity` by depositor address, per pool"). [5](#0-4) 

---

### Likelihood Explanation

The trigger requires no special privilege, no malicious token, and no admin cooperation. Any EOA or contract can call `addLiquidity` on a pool that uses this extension, supplying any allowlisted address as `owner`. The allowlisted address need not be a contract; any on-chain address that the pool admin has whitelisted suffices. The attack is repeatable and costs only gas plus the deposited tokens (which the attacker loses to the allowlisted owner's position).

---

### Recommendation

Replace the `owner` check with `sender` in `beforeAddLiquidity`, mirroring the pattern used in `SwapAllowlistExtension`:

```solidity
// Before (incorrect):
if (!allowAllDepositors[msg.sender] && !allowedDepositor[msg.sender][owner]) {

// After (correct):
if (!allowAllDepositors[msg.sender] && !allowedDepositor[msg.sender][sender]) {
``` [3](#0-2) 

If the intended semantics are to gate by position owner (not caller), the extension's NatSpec and `setAllowedToDeposit` parameter name should be updated to make this explicit, and the router/liquidity adder must be audited to ensure it cannot be used to route deposits on behalf of allowlisted owners.

---

### Proof of Concept

1. Pool is deployed with `DepositAllowlistExtension` configured on `beforeAddLiquidity`.
2. Pool admin calls `setAllowedToDeposit(pool, alice, true)`. Bob is not allowlisted.
3. Bob calls `pool.addLiquidity(alice, salt, deltas, callbackData, extensionData)`.
4. Pool calls `_beforeAddLiquidity(bob, alice, salt, deltas, extensionData)`.
5. Extension evaluates `allowedDepositor[pool][alice]` → `true` → no revert.
6. `LiquidityLib.addLiquidity` executes; the swap callback fires against Bob (`msg.sender`), pulling Bob's tokens.
7. Alice's position is credited with the LP shares.
8. Bob has lost his tokens; Alice holds an unwanted position; the allowlist has been bypassed. [6](#0-5) [3](#0-2)

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

**File:** metric-core/contracts/ExtensionCalling.sol (L95-98)
```text
    _callExtensionsInOrder(
      BEFORE_ADD_LIQUIDITY_ORDER,
      abi.encodeCall(IMetricOmmExtensions.beforeAddLiquidity, (sender, owner, salt, deltas, extensionData))
    );
```

**File:** metric-periphery/contracts/extensions/DepositAllowlistExtension.sol (L12-12)
```text
contract DepositAllowlistExtension is BaseMetricExtension, IDepositAllowlistExtension {
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
