### Title
`DepositAllowlistExtension.beforeAddLiquidity` Ignores `sender`, Allowing Non-Allowlisted Operators to Bypass the Deposit Guard — (`metric-periphery/contracts/extensions/DepositAllowlistExtension.sol`)

---

### Summary

`DepositAllowlistExtension.beforeAddLiquidity` silently drops the `sender` argument (the actual `msg.sender` of `addLiquidity`) and gates only on `owner` (the position recipient). Because `MetricOmmPool.addLiquidity` explicitly supports an operator pattern where `msg.sender ≠ owner`, any non-allowlisted address can bypass the deposit guard by supplying an allowlisted owner address. This is the direct analog of the external bug: a caller-controlled identifier (`owner`) is used as the sole validation key, while the entity actually performing the action (`sender`) is never checked.

---

### Finding Description

`MetricOmmPool.addLiquidity` passes `msg.sender` as `sender` and the caller-supplied `owner` as the position recipient into the extension hook: [1](#0-0) 

The pool's own NatSpec confirms the operator pattern is intentional: [2](#0-1) 

`DepositAllowlistExtension.beforeAddLiquidity` receives both addresses but discards `sender` (the first parameter is unnamed) and checks only `owner`: [3](#0-2) 

The allowlist mapping is keyed `pool → depositor`: [4](#0-3) 

By contrast, `SwapAllowlistExtension.beforeSwap` correctly checks `sender` (the actual caller of `pool.swap()`): [5](#0-4) 

The inconsistency is structural: the swap guard gates on who performs the action; the deposit guard gates only on who receives the position. Because `owner` is a free parameter supplied by the caller, any non-allowlisted address can name an allowlisted owner and pass the check.

---

### Impact Explanation

The deposit allowlist — a pool-admin-configured access control — is bypassed by an unprivileged path. A non-allowlisted entity can inject liquidity into the pool (paying tokens via the `IMetricOmmModifyLiquidityCallback` callback) while the guard records the action as belonging to an allowlisted owner. This breaks the admin-boundary invariant: the pool admin's intent to restrict who may provide liquidity is circumvented without any elevated privilege. Downstream consequences include non-compliant liquidity entering pools that are meant to be restricted (e.g., permissioned institutional pools), and the allowlisted owner receiving an unsolicited position they did not initiate.

---

### Likelihood Explanation

Exploitation requires no special privilege. Any address can call `pool.addLiquidity(allowlisted_owner, ...)` directly. The operator pattern is a documented, first-class feature of the pool, so the call path is always open. The only prerequisite is knowing at least one allowlisted owner address, which is publicly readable from `allowedDepositor`.

---

### Recommendation

Check `sender` (the actual caller) in `beforeAddLiquidity`, consistent with how `SwapAllowlistExtension` handles `beforeSwap`. If the intent is to gate on who performs the deposit action:

```solidity
function beforeAddLiquidity(address sender, address owner, uint80, LiquidityDelta calldata, bytes calldata)
    external view override returns (bytes4)
{
    if (!allowAllDepositors[msg.sender]
        && !allowedDepositor[msg.sender][sender])   // check the actual caller
    {
        revert IMetricOmmPoolActions.NotAllowedToDeposit();
    }
    return IMetricOmmExtensions.beforeAddLiquidity.selector;
}
```

If both the operator and the owner must be allowlisted, check both. The fix must be consistent with the stated semantics of the allowlist.

---

### Proof of Concept

1. Pool admin sets `allowedDepositor[pool][alice] = true`; Bob is **not** allowlisted.
2. Bob calls `pool.addLiquidity(alice, salt, deltas, callbackData, "")`.
3. Pool calls `_beforeAddLiquidity(Bob, alice, ...)`.
4. `DepositAllowlistExtension.beforeAddLiquidity` receives `sender=Bob, owner=alice`; ignores `Bob`; checks `allowedDepositor[pool][alice]` → `true` → passes.
5. `LiquidityLib.addLiquidity` credits the position to `alice`; the callback pulls tokens from `Bob`.
6. Bob has deposited into a restricted pool without being allowlisted. The guard is fully bypassed. [3](#0-2) [6](#0-5)

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

**File:** metric-core/contracts/interfaces/IMetricOmmPool/IMetricOmmPoolActions.sol (L147-147)
```text
  /// @dev Callback receives native token amounts the pool expects; underpay reverts `InsufficientTokenBalance`. If `DEPOSIT_ALLOWLIST_PROVIDER` is set, `owner` must pass allowlist. `msg.sender` pays but need not equal `owner` (operator pattern).
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
