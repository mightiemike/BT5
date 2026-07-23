### Title
`DepositAllowlistExtension.beforeAddLiquidity` Checks `owner` Instead of `sender`, Allowing Any Caller to Bypass the Deposit Allowlist — (File: `metric-periphery/contracts/extensions/DepositAllowlistExtension.sol`)

---

### Summary

The `DepositAllowlistExtension` is documented as gating `addLiquidity` **by depositor address**. Its `beforeAddLiquidity` hook silently drops the `sender` argument and instead validates the caller-supplied `owner` parameter. Because `owner` is a free argument in `MetricOmmPool.addLiquidity`, any address — including one that is not on the allowlist — can pass the guard by nominating any allowlisted address as `owner`. The LP shares are minted to that allowlisted address, but the actual token deposit is made by the unauthorized caller. The `SwapAllowlistExtension`, the symmetric sibling, correctly checks `sender`; the deposit extension does not.

---

### Finding Description

`MetricOmmPool.addLiquidity` accepts a caller-supplied `owner` and passes both `msg.sender` (as `sender`) and `owner` to the extension hook: [1](#0-0) 

`ExtensionCalling._beforeAddLiquidity` forwards both values faithfully: [2](#0-1) 

`DepositAllowlistExtension.beforeAddLiquidity` then silently discards `sender` (first parameter is unnamed) and checks only `owner`: [3](#0-2) 

The mapping is named `allowedDepositor` and the NatSpec says *"Gates `addLiquidity` by depositor address"*, confirming the intent is to restrict the actual token provider, not the LP-position recipient: [4](#0-3) 

By contrast, `SwapAllowlistExtension.beforeSwap` correctly reads and checks `sender`: [5](#0-4) 

**Attack path:**

1. Pool is deployed with `DepositAllowlistExtension`; pool admin sets `allowedDepositor[pool][alice] = true`.
2. Bob (not on the allowlist) calls `pool.addLiquidity(alice, salt, deltas, callbackData, extensionData)`.
3. The pool calls `_beforeAddLiquidity(bob /*sender*/, alice /*owner*/, ...)`.
4. The extension ignores `bob` and checks `allowedDepositor[pool][alice]` → `true` → no revert.
5. Bob's tokens are pulled via the swap callback; LP shares are minted to `alice`.
6. Alice (a colluding EOA, or a contract that auto-returns tokens) transfers the shares or the withdrawn tokens back to Bob.

The allowlist is fully bypassed: Bob deposited into a pool he was explicitly excluded from.

---

### Impact Explanation

The deposit allowlist is a pool-admin-configured security boundary. Its entire purpose is to prevent unauthorized addresses from depositing into the pool (e.g., for regulatory compliance, private/institutional pools, or phased liquidity programs). Because the check is on the wrong address, the boundary is trivially circumvented by any caller who knows a single allowlisted address — a piece of public on-chain information. The pool admin's security invariant ("only approved depositors can add liquidity") is broken for every pool that deploys this extension.

---

### Likelihood Explanation

The bypass requires only that the attacker supply an allowlisted address as `owner`. Allowlisted addresses are visible on-chain via the public `allowedDepositor` mapping. No special privilege, flash loan, or oracle manipulation is needed. The only coordination required is that the allowlisted `owner` eventually returns the LP shares or withdrawn tokens; this is trivially arranged with a cooperating contract as `owner`. Likelihood is **high**.

---

### Recommendation

Check `sender` (the actual depositor) instead of `owner` (the LP-position recipient), mirroring `SwapAllowlistExtension`:

```solidity
// DepositAllowlistExtension.sol
function beforeAddLiquidity(
    address sender,   // ← use this, not owner
    address,
    uint80,
    LiquidityDelta calldata,
    bytes calldata
) external view override returns (bytes4) {
    if (!allowAllDepositors[msg.sender] && !allowedDepositor[msg.sender][sender]) {
        revert IMetricOmmPoolActions.NotAllowedToDeposit();
    }
    return IMetricOmmExtensions.beforeAddLiquidity.selector;
}
```

---

### Proof of Concept

```solidity
// SPDX-License-Identifier: MIT
pragma solidity ^0.8.35;

// Assume:
//   pool  — MetricOmmPool with DepositAllowlistExtension configured
//   ext   — DepositAllowlistExtension instance
//   alice — allowedDepositor[pool][alice] == true
//   bob   — allowedDepositor[pool][bob]   == false

// Pool admin state (set during setup):
//   ext.setAllowedToDeposit(pool, alice, true);

// Bob's attack:
// Bob calls addLiquidity with owner = alice.
// The extension checks allowedDepositor[pool][alice] → true → no revert.
// Bob's tokens are pulled; alice receives LP shares.
pool.addLiquidity(
    alice,          // owner  ← allowlisted; extension checks this
    salt,
    deltas,
    callbackData,   // pulls tokens from bob (msg.sender) via callback
    extensionData
);

// Alice (or a contract acting as alice) withdraws and returns tokens to bob.
// Bob has successfully deposited into a pool he was excluded from.
```

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
