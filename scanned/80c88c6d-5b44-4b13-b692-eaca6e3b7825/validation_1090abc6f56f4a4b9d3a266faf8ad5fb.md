Looking at the repository, I need to trace the extension guard plumbing for `DepositAllowlistExtension` and compare it against `SwapAllowlistExtension` to find the analog to the "wrong actor checked in a guard" class of bug.

### Title
`DepositAllowlistExtension.beforeAddLiquidity` checks `owner` instead of `sender`, allowing any unprivileged caller to bypass the deposit allowlist — (`metric-periphery/contracts/extensions/DepositAllowlistExtension.sol`)

---

### Summary

`DepositAllowlistExtension` is supposed to gate who may add liquidity to a permissioned pool. Its `beforeAddLiquidity` hook receives both `sender` (the actual `msg.sender` of `addLiquidity`, i.e., the token provider) and `owner` (the LP-position beneficiary), but silently ignores `sender` and only validates `owner`. Any unprivileged address can therefore call `pool.addLiquidity(owner = allowlisted_address, …)`, pass the allowlist check, and inject tokens into the pool — breaking the permissioned-pool invariant.

---

### Finding Description

`MetricOmmPool.addLiquidity` passes `msg.sender` as `sender` and the caller-supplied `owner` as the LP-position owner to the extension hook:

```solidity
// MetricOmmPool.sol line 191
_beforeAddLiquidity(msg.sender, owner, salt, deltas, extensionData);
``` [1](#0-0) 

The extension receives `sender` as its first parameter but never uses it. The allowlist check is performed exclusively on `owner`:

```solidity
// DepositAllowlistExtension.sol lines 32-42
function beforeAddLiquidity(address, address owner, uint80, LiquidityDelta calldata, bytes calldata)
  external view override returns (bytes4)
{
  if (!allowAllDepositors[msg.sender] && !allowedDepositor[msg.sender][owner]) {
    revert IMetricOmmPoolActions.NotAllowedToDeposit();
  }
  return IMetricOmmExtensions.beforeAddLiquidity.selector;
}
``` [2](#0-1) 

The first parameter (`sender`) is unnamed/discarded. Compare this with `SwapAllowlistExtension`, which correctly validates the actual caller:

```solidity
// SwapAllowlistExtension.sol line 37
if (!allowAllSwappers[msg.sender] && !allowedSwapper[msg.sender][sender]) {
``` [3](#0-2) 

The inconsistency is structural: `SwapAllowlistExtension` gates on `sender` (the economic actor providing tokens), while `DepositAllowlistExtension` gates on `owner` (the LP-share recipient). The token provider — the party that actually injects assets into the pool — is never checked.

---

### Impact Explanation

An unauthorized `sender` (not on the allowlist) can call `pool.addLiquidity(owner = allowlisted_address, …)`. The extension checks `allowedDepositor[pool][allowlisted_address]` → passes. The pool's callback fires on `sender`, pulling tokens from the unauthorized party. LP shares are credited to `allowlisted_address`.

Concrete consequences:

1. **Permissioned-pool invariant broken**: Any address can inject tokens into a KYC/compliance-gated pool, defeating the entire purpose of the extension.
2. **Griefing / forced LP exposure**: An attacker can force an allowlisted address to accumulate LP shares it never requested, exposing it to impermanent loss and requiring gas to unwind.
3. **Allowlisted address windfall / attacker loss**: If the attacker controls the allowlisted `owner` address (e.g., a contract they deployed that was whitelisted), they can later call `removeLiquidity` from that address (since `removeLiquidity` enforces `msg.sender == owner`) and recover the tokens — effectively making the deposit allowlist a no-op for any party that controls a whitelisted address. [4](#0-3) 

---

### Likelihood Explanation

- No special privileges required: any EOA or contract can call `pool.addLiquidity` with an arbitrary `owner`.
- The only precondition is knowing an allowlisted address, which is readable on-chain from `allowedDepositor`.
- The `MetricOmmPoolLiquidityAdder` periphery contract is a natural `sender` that pools are expected to interact with; any user of that router can set `owner` to an allowlisted address and bypass the gate. [5](#0-4) 

---

### Recommendation

Change the `beforeAddLiquidity` check to validate `sender` (the actual token provider) instead of `owner`:

```solidity
function beforeAddLiquidity(address sender, address owner, uint80, LiquidityDelta calldata, bytes calldata)
  external view override returns (bytes4)
{
  // Gate on the actual token provider, not the LP-share recipient.
  if (!allowAllDepositors[msg.sender] && !allowedDepositor[msg.sender][sender]) {
    revert IMetricOmmPoolActions.NotAllowedToDeposit();
  }
  return IMetricOmmExtensions.beforeAddLiquidity.selector;
}
```

If the intent is to gate on `owner` (e.g., to allow a trusted router as `sender`), then both `sender` and `owner` should be validated, or the NatSpec must explicitly document the design choice so pool admins understand that any address can inject tokens as long as the LP-share recipient is allowlisted.

---

### Proof of Concept

```
Setup:
  pool P configured with DepositAllowlistExtension E
  allowedDepositor[P][Alice] = true   // Alice is KYC'd
  allowedDepositor[P][Bob]   = false  // Bob is NOT KYC'd

Attack:
  Bob (or a contract Bob controls) calls:
    P.addLiquidity(owner=Alice, salt=0, deltas=..., callbackData=..., extensionData=...)

  Inside the pool:
    _beforeAddLiquidity(sender=Bob, owner=Alice, ...)
    → E.beforeAddLiquidity(Bob, Alice, ...)
    → checks allowedDepositor[P][Alice] == true  ← PASSES (Bob never checked)
    → LP shares minted to Alice
    → callback fires on Bob → Bob's tokens transferred to pool

Result:
  Bob (unauthorized) has injected tokens into the permissioned pool.
  If Bob controls Alice's address (e.g., Alice is a contract Bob deployed
  that was whitelisted), Bob calls Alice.removeLiquidity(...) and recovers
  the tokens — the allowlist is fully bypassed.
``` [2](#0-1) [6](#0-5)

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

**File:** metric-core/contracts/MetricOmmPool.sol (L199-212)
```text
  function removeLiquidity(address owner, uint80 salt, LiquidityDelta calldata deltas, bytes calldata extensionData)
    external
    nonReentrant(PoolActions.REMOVE_LIQUIDITY)
    returns (uint256 amount0Removed, uint256 amount1Removed)
  {
    if (deltas.binIdxs.length == 0) return (0, 0);
    if (deltas.binIdxs.length != deltas.shares.length) revert LiquidityDeltaLengthMismatch();
    if (msg.sender != owner) revert NotPositionOwner();
    _beforeRemoveLiquidity(msg.sender, owner, salt, deltas, extensionData);
    (amount0Removed, amount1Removed) = LiquidityLib.removeLiquidity(
      _liquidityContext(), owner, salt, deltas, binTotals, _binStates, _binTotalShares, _positionBinShares
    );
    _afterRemoveLiquidity(msg.sender, owner, salt, deltas, amount0Removed, amount1Removed, extensionData);
  }
```

**File:** metric-periphery/contracts/extensions/DepositAllowlistExtension.sol (L13-14)
```text
  mapping(address pool => mapping(address depositor => bool)) public allowedDepositor;
  mapping(address pool => bool) public allowAllDepositors;
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

**File:** metric-periphery/contracts/extensions/SwapAllowlistExtension.sol (L37-39)
```text
    if (!allowAllSwappers[msg.sender] && !allowedSwapper[msg.sender][sender]) {
      revert IMetricOmmPoolActions.NotAllowedToSwap();
    }
```
