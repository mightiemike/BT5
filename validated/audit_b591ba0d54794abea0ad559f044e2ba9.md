### Title
`DepositAllowlistExtension.beforeAddLiquidity` checks LP position `owner` instead of transaction `sender`, allowing any unprivileged address to bypass the deposit allowlist — (File: `metric-periphery/contracts/extensions/DepositAllowlistExtension.sol`)

---

### Summary

`DepositAllowlistExtension` is documented as gating `addLiquidity` by **depositor address**. Its `beforeAddLiquidity` hook silently ignores the `sender` argument (the actual `msg.sender` of `addLiquidity`) and instead checks the `owner` argument (the LP-position owner, a caller-supplied parameter). Any address — including one not on the allowlist — can bypass the guard by supplying an allowlisted address as `owner`.

---

### Finding Description

`MetricOmmPool.addLiquidity` calls the extension hook with both the real caller and the caller-supplied owner:

```solidity
// MetricOmmPool.sol line 191
_beforeAddLiquidity(msg.sender, owner, salt, deltas, extensionData);
```

`ExtensionCalling._beforeAddLiquidity` forwards both to the extension:

```solidity
// ExtensionCalling.sol lines 88-99
abi.encodeCall(IMetricOmmExtensions.beforeAddLiquidity, (sender, owner, salt, deltas, extensionData))
```

`DepositAllowlistExtension.beforeAddLiquidity` receives `sender` as its first argument but discards it (unnamed `address,`) and checks only `owner`:

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
```

Compare with `SwapAllowlistExtension.beforeSwap`, which correctly checks `sender` (the actual caller) and discards the second argument (`recipient`):

```solidity
// SwapAllowlistExtension.sol lines 31-41
function beforeSwap(address sender, address, bool, int128, uint128, uint256, uint128, uint128, bytes calldata)
    external view override returns (bytes4)
{
    if (!allowAllSwappers[msg.sender] && !allowedSwapper[msg.sender][sender]) {
        revert IMetricOmmPoolActions.NotAllowedToSwap();
    }
    return IMetricOmmExtensions.beforeSwap.selector;
}
```

The two sibling extensions apply opposite logic: the swap guard checks the actual caller; the deposit guard checks the LP-position owner. The deposit guard is wrong.

---

### Impact Explanation

Any address not on the allowlist can call:

```solidity
pool.addLiquidity(
    owner = <any allowlisted address>,
    salt  = <any value>,
    deltas = <desired liquidity>,
    ...
);
```

The `beforeAddLiquidity` hook checks `allowedDepositor[pool][owner]`, which is `true` for the supplied allowlisted address, so the revert is never reached. The unauthorized `sender` provides tokens via the swap callback; the LP shares are credited to the allowlisted `owner`.

Consequences:
- The pool admin's deposit allowlist is fully bypassed by any unprivileged address. The guard that was supposed to restrict who can add liquidity to the pool does not restrict the actual depositor at all.
- If the allowlist is used for regulatory compliance (KYC/AML), sanctioned or non-compliant addresses can deposit freely.
- The allowlisted `owner` receives LP shares they did not request, increasing their exposure without consent (griefing vector).
- The pool admin has no on-chain mechanism to prevent this once the pool is deployed, because the extension addresses are immutable.

---

### Likelihood Explanation

The bypass requires only knowledge of one allowlisted address (publicly readable from `allowedDepositor`) and the ability to call `addLiquidity` directly or through any router. No special privilege, flash loan, or oracle manipulation is needed. The attacker must supply tokens (they pay), so the attack is not directly profitable, but it is trivially executable by any address that wants to circumvent the access control for any reason (regulatory evasion, griefing, pool composition manipulation).

---

### Recommendation

Replace the ignored first argument with `sender` and check it instead of `owner`, matching the pattern used by `SwapAllowlistExtension`:

```solidity
// DepositAllowlistExtension.sol
function beforeAddLiquidity(address sender, address, uint80, LiquidityDelta calldata, bytes calldata)
    external view override returns (bytes4)
{
    if (!allowAllDepositors[msg.sender] && !allowedDepositor[msg.sender][sender]) {
        revert IMetricOmmPoolActions.NotAllowedToDeposit();
    }
    return IMetricOmmExtensions.beforeAddLiquidity.selector;
}
```

If the intended semantics are to restrict by LP-position owner (not caller), the contract name, NatSpec, and admin documentation must be updated to reflect that, and the `setAllowedToDeposit` / `setAllowedToSwap` naming asymmetry must be resolved.

---

### Proof of Concept

```solidity
// Assume pool has DepositAllowlistExtension configured.
// Alice (0xAlice) is on the allowlist; Bob (0xBob) is not.

// Bob calls addLiquidity with owner = Alice.
// The extension checks allowedDepositor[pool][Alice] == true → no revert.
// Bob pays tokens via callback; Alice's LP position is credited.

pool.addLiquidity(
    /* owner */        address(alice),
    /* salt */         0,
    /* deltas */       liquidityDelta,
    /* callbackData */ "",
    /* extensionData */""
);
// Succeeds despite Bob not being on the allowlist.
``` [1](#0-0) [2](#0-1) [3](#0-2) 
<cite repo="patrichyt/2026-07-metric-dev-oyakhil-

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
