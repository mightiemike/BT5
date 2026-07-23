### Title
`DepositAllowlistExtension` Guards the Wrong Address — Unauthorized Depositors Bypass the Allowlist by Setting `owner` to an Allowlisted Address - (File: `metric-periphery/contracts/extensions/DepositAllowlistExtension.sol`)

---

### Summary

`DepositAllowlistExtension.beforeAddLiquidity` is documented as gating `addLiquidity` **by depositor address**, but its guard checks the `owner` parameter (the LP-position recipient) instead of the `sender` parameter (the address that actually initiates the call and pays the tokens via callback). Any unprivileged address can bypass the allowlist by passing an allowlisted address as `owner`.

---

### Finding Description

`MetricOmmPool.addLiquidity` accepts two distinct address arguments:

- `msg.sender` → forwarded to extensions as `sender` — the caller who initiates the transaction and whose tokens are pulled via the swap-callback mechanism.
- `owner` (caller-supplied) → forwarded to extensions as `owner` — the address that receives the LP shares. [1](#0-0) 

`ExtensionCalling._beforeAddLiquidity` passes both to the extension: [2](#0-1) 

`DepositAllowlistExtension.beforeAddLiquidity` silently discards the first argument (`sender`) and checks only `owner`:

```solidity
function beforeAddLiquidity(address, address owner, uint80, LiquidityDelta calldata, bytes calldata)
    external view override returns (bytes4)
{
    if (!allowAllDepositors[msg.sender] && !allowedDepositor[msg.sender][owner]) {
        revert IMetricOmmPoolActions.NotAllowedToDeposit();
    }
    return IMetricOmmExtensions.beforeAddLiquidity.selector;
}
``` [3](#0-2) 

Because `owner` is a **caller-supplied** parameter with no on-chain binding to the actual token source, any address can pass an allowlisted address as `owner` and the guard succeeds. The pool then pulls tokens from the unauthorized `sender` via callback and credits the LP position to the supplied `owner`.

The admin-facing setter names the parameter `depositor`, confirming the intent is to gate the actual depositing party: [4](#0-3) 

---

### Impact Explanation

The pool admin's deposit allowlist is completely ineffective. Any address — regardless of allowlist status — can deposit tokens into a restricted pool by supplying an allowlisted address as `owner`. The unauthorized depositor loses their tokens (pulled via callback), the allowlisted address receives LP shares it never requested, and the pool admin's access-control invariant is violated. For pools configured as private/institutional/KYC-gated liquidity venues, this breaks the core restriction the extension was deployed to enforce.

---

### Likelihood Explanation

The bypass requires only a single `addLiquidity` call with a publicly observable allowlisted address as `owner`. No special privileges, flash loans, or multi-step setup are needed. Any actor who can read the allowlist state (public mapping) can execute the bypass immediately.

---

### Recommendation

Replace the `owner` check with a `sender` check to gate the actual depositing party:

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

If the intent is instead to gate LP-position ownership (i.e., restrict who may *hold* shares), the parameter name and NatSpec should be updated to `owner` and the setter renamed accordingly, so the semantics are unambiguous.

---

### Proof of Concept

1. Pool admin deploys pool with `DepositAllowlistExtension` and calls `setAllowedToDeposit(pool, Alice, true)`. Bob is **not** allowlisted.
2. Bob calls `pool.addLiquidity(Alice, salt, deltas, callbackData, extensionData)`.
3. Pool calls `extension.beforeAddLiquidity(Bob /*sender*/, Alice /*owner*/, ...)`.
4. Guard evaluates `allowedDepositor[pool][Alice]` → `true` → **no revert**.
5. Pool executes `LiquidityLib.addLiquidity`, callback pulls tokens from Bob, LP shares are credited to Alice.
6. Bob has deposited into a pool he is not allowlisted for; the allowlist is bypassed. [3](#0-2) [5](#0-4)

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

**File:** metric-periphery/contracts/extensions/DepositAllowlistExtension.sol (L18-21)
```text
  function setAllowedToDeposit(address pool_, address depositor, bool allowed) external onlyPoolAdmin(pool_) {
    allowedDepositor[pool_][depositor] = allowed;
    emit AllowedToDepositSet(pool_, depositor, allowed);
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
