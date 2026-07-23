### Title
`DepositAllowlistExtension` Checks LP Position `owner` Instead of Transaction `sender`, Allowing Any Address to Bypass the Deposit Allowlist — (`metric-periphery/contracts/extensions/DepositAllowlistExtension.sol`)

---

### Summary

`DepositAllowlistExtension.beforeAddLiquidity` is documented as gating `addLiquidity` **by depositor address**, but its implementation checks the caller-supplied `owner` parameter (the LP position beneficiary) rather than `sender` (the actual transaction originator who pays the tokens). Because `owner` is a free argument to `addLiquidity`, any un-allowlisted address can bypass the guard by naming an allowlisted address as `owner`.

---

### Finding Description

`MetricOmmPool.addLiquidity` accepts a caller-supplied `owner` address and passes both `msg.sender` (as `sender`) and `owner` to the extension hook: [1](#0-0) 

`ExtensionCalling._beforeAddLiquidity` forwards both values verbatim to every configured extension: [2](#0-1) 

`DepositAllowlistExtension.beforeAddLiquidity` then performs its allowlist check exclusively against `owner`, ignoring `sender` entirely: [3](#0-2) 

The allowlist mappings are keyed `allowedDepositor[pool][owner]`: [4](#0-3) 

There is no check anywhere in `addLiquidity` that `msg.sender == owner`. The only place that enforces caller-equals-owner is `removeLiquidity`: [5](#0-4) 

---

### Impact Explanation

A pool admin deploys `DepositAllowlistExtension` to restrict liquidity provision to a curated set of addresses (e.g., KYC'd LPs, institutional partners). The guard is silently inert against any caller who simply passes an allowlisted address as `owner`:

- The un-allowlisted caller (`sender`) pays the tokens via the swap callback.
- The allowlisted `owner` receives the LP shares without having initiated or consented to the deposit.
- The pool's intended access-control boundary is fully bypassed; the pool admin's configuration has no effect on who can actually deposit.

This breaks the core allowlist invariant stated in the audit pivots: *"deposit/swap allowlist checks must cover the exact actor/action intended and cannot be bypassed through … owner/salt separation."* The bypass is unconditional — it requires no special privilege, no malicious token, and no admin cooperation.

**Severity: Medium** — broken core pool access-control functionality; no direct theft of existing LP principal, but the allowlist protection the pool admin configured is rendered completely ineffective for any caller willing to name an allowlisted address as `owner`.

---

### Likelihood Explanation

Exploitation requires only knowledge of one allowlisted address (readable from `allowedDepositor` public mapping or from on-chain events) and the ability to call `addLiquidity` directly or through any router. No special role or privileged access is needed. Any externally-owned account or contract can perform this in a single transaction.

---

### Recommendation

Change `beforeAddLiquidity` to check `sender` (the first parameter, which is `msg.sender` of the pool's `addLiquidity` call) rather than `owner`:

```solidity
// current — wrong field
function beforeAddLiquidity(address, address owner, uint80, LiquidityDelta calldata, bytes calldata)
    external view override returns (bytes4)
{
    if (!allowAllDepositors[msg.sender] && !allowedDepositor[msg.sender][owner]) {
        revert IMetricOmmPoolActions.NotAllowedToDeposit();
    }
    ...
}

// fixed — check the actual caller
function beforeAddLiquidity(address sender, address, uint80, LiquidityDelta calldata, bytes calldata)
    external view override returns (bytes4)
{
    if (!allowAllDepositors[msg.sender] && !allowedDepositor[msg.sender][sender]) {
        revert IMetricOmmPoolActions.NotAllowedToDeposit();
    }
    ...
}
```

If the intent is to restrict both the caller and the position owner, both fields should be checked.

---

### Proof of Concept

```
Setup:
  pool  = MetricOmmPool with DepositAllowlistExtension configured
  Alice = allowedDepositor[pool][Alice] = true
  Bob   = allowedDepositor[pool][Bob]   = false  (not allowlisted)

Attack (single tx, no special role):
  Bob calls pool.addLiquidity(
      owner        = Alice,   // allowlisted address — passes the guard
      salt         = 0,
      deltas       = <desired liquidity>,
      callbackData = <Bob pays tokens in metricOmmSwapCallback>,
      extensionData = ""
  )

Result:
  - beforeAddLiquidity checks allowedDepositor[pool][Alice] → true → no revert
  - Bob's tokens are transferred to the pool via callback
  - LP shares are credited to Alice's position (owner=Alice, salt=0)
  - Bob has bypassed the deposit allowlist entirely
  - Pool admin's restriction on Bob is silently ineffective
``` [3](#0-2) [6](#0-5)

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

**File:** metric-periphery/contracts/extensions/DepositAllowlistExtension.sol (L12-14)
```text
contract DepositAllowlistExtension is BaseMetricExtension, IDepositAllowlistExtension {
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
